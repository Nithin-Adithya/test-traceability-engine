"""Semantic matching of test cases to requirements.

Every test is embedded and searched against a FAISS index of requirement embeddings.
A test is linked to its best-matching requirement when the cosine similarity clears
`threshold`, and to any runner-up requirement scoring within `margin` of that best match.
Requirements left with no linked test are reported as uncovered; tests left with no
linked requirement are reported as orphans.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from time import perf_counter
from typing import Sequence

import faiss
import numpy as np

from traceability.embedder import Embedder
from traceability.models import Link, Requirement, TestCase

Neighbours = dict[str, list[tuple[str, float]]]


@dataclass(frozen=True)
class TraceConfig:
    threshold: float = 0.35
    margin: float = 0.0
    top_k: int = 5

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        if self.margin < 0.0:
            raise ValueError("margin must not be negative")
        if self.top_k < 1:
            raise ValueError("top_k must be at least 1")


def select_links(candidates: Neighbours, threshold: float, margin: float) -> list[Link]:
    """Turn each test's ranked requirement candidates into accepted links."""
    links = []
    for test_id, ranked in candidates.items():
        if not ranked or ranked[0][1] < threshold:
            continue
        best = ranked[0][1]
        links.extend(Link(req_id, test_id, score) for req_id, score in ranked
                     if score >= threshold and score >= best - margin)
    return links


@dataclass
class TraceResult:
    requirements: list[Requirement]
    tests: list[TestCase]
    candidates: Neighbours       # test id -> requirements ranked by similarity
    nearest_tests: Neighbours    # requirement id -> tests ranked by similarity
    config: TraceConfig
    timings: dict[str, float] = field(default_factory=dict)
    links: list[Link] = field(init=False)

    def __post_init__(self) -> None:
        self.links = select_links(self.candidates, self.config.threshold, self.config.margin)

    def with_config(self, config: TraceConfig) -> TraceResult:
        """Re-derive links under different thresholds without re-embedding."""
        return replace(self, config=config)

    @property
    def coverage(self) -> dict[str, list[Link]]:
        covered: dict[str, list[Link]] = {r.id: [] for r in self.requirements}
        for link in self.links:
            covered[link.requirement_id].append(link)
        for links in covered.values():
            links.sort(key=lambda link: -link.score)
        return covered

    def matrix(self) -> list[dict]:
        coverage = self.coverage
        rows = []
        for req in self.requirements:
            links = coverage[req.id]
            rows.append({
                "requirement_id": req.id,
                "requirement": req.embedding_text,
                "status": "Covered" if links else "Uncovered",
                "test_count": len(links),
                "tests": ", ".join(link.test_id for link in links),
                "best_score": round(links[0].score, 3) if links else None,
            })
        return rows

    def uncovered(self) -> list[dict]:
        coverage = self.coverage
        titles = {t.id: t.embedding_text for t in self.tests}
        rows = []
        for req in self.requirements:
            if coverage[req.id]:
                continue
            nearest = self.nearest_tests.get(req.id) or [(None, None)]
            test_id, score = nearest[0]
            rows.append({
                "requirement_id": req.id,
                "requirement": req.embedding_text,
                "closest_test_id": test_id,
                "closest_test": titles.get(test_id, ""),
                "closest_score": round(score, 3) if score is not None else None,
            })
        return rows

    def orphans(self) -> list[dict]:
        linked = {link.test_id for link in self.links}
        texts = {r.id: r.embedding_text for r in self.requirements}
        rows = []
        for test in self.tests:
            if test.id in linked:
                continue
            ranked = self.candidates.get(test.id) or [(None, None)]
            req_id, score = ranked[0]
            rows.append({
                "test_id": test.id,
                "test": test.embedding_text,
                "closest_requirement_id": req_id,
                "closest_requirement": texts.get(req_id, ""),
                "closest_score": round(score, 3) if score is not None else None,
            })
        return rows

    def summary(self) -> dict:
        uncovered = sum(1 for links in self.coverage.values() if not links)
        orphans = len(self.tests) - len({link.test_id for link in self.links})
        total = len(self.requirements)
        return {
            "requirements": total,
            "tests": len(self.tests),
            "links": len(self.links),
            "covered_requirements": total - uncovered,
            "uncovered_requirements": uncovered,
            "orphan_tests": orphans,
            "coverage_pct": round(100.0 * (total - uncovered) / total, 1),
            "config": asdict(self.config),
            "timings_s": {k: round(v, 3) for k, v in self.timings.items()},
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "matrix": self.matrix(),
            "links": [{"requirement_id": l.requirement_id, "test_id": l.test_id, "score": round(l.score, 3)}
                      for l in sorted(self.links, key=lambda l: (l.requirement_id, -l.score))],
            "uncovered": self.uncovered(),
            "orphans": self.orphans(),
        }


class TraceabilityEngine:
    def __init__(self, embedder: Embedder, config: TraceConfig | None = None) -> None:
        self.embedder = embedder
        self.config = config or TraceConfig()

    def trace(self, requirements: Sequence[Requirement], tests: Sequence[TestCase]) -> TraceResult:
        if not requirements:
            raise ValueError("At least one requirement is needed.")
        if not tests:
            raise ValueError("At least one test case is needed.")

        started = perf_counter()
        req_vectors = self.embedder.encode([r.embedding_text for r in requirements])
        test_vectors = self.embedder.encode([t.embedding_text for t in tests])
        embedded = perf_counter()

        req_ids = [r.id for r in requirements]
        test_ids = [t.id for t in tests]
        candidates = _search(req_vectors, req_ids, test_vectors, test_ids, self.config.top_k)
        nearest_tests = _search(test_vectors, test_ids, req_vectors, req_ids, 3)
        matched = perf_counter()

        return TraceResult(
            requirements=list(requirements),
            tests=list(tests),
            candidates=candidates,
            nearest_tests=nearest_tests,
            config=self.config,
            timings={"embedding": embedded - started, "matching": matched - embedded,
                     "total": matched - started},
        )


def _search(corpus: np.ndarray, corpus_ids: list[str], queries: np.ndarray, query_ids: list[str],
            k: int) -> Neighbours:
    """Top-k corpus items per query by cosine similarity (vectors are L2-normalised)."""
    index = faiss.IndexFlatIP(corpus.shape[1])
    index.add(corpus)
    scores, positions = index.search(queries, min(k, len(corpus_ids)))
    return {
        query_id: [(corpus_ids[p], float(s)) for s, p in zip(row_scores, row_positions) if p >= 0]
        for query_id, row_scores, row_positions in zip(query_ids, scores, positions)
    }
