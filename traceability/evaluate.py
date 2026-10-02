"""Score a trace result against labelled requirement-to-test pairs."""

from __future__ import annotations

from pathlib import Path

from traceability.engine import TraceConfig, TraceResult
from traceability.parsers import ParseError, _csv_rows, _normalise, _xlsx_tables

_REQ_HEADERS = {"requirementid", "reqid", "requirement"}
_TEST_HEADERS = {"testid", "testcaseid", "tcid", "test", "testcase"}


def load_ground_truth(path: str | Path) -> set[tuple[str, str]]:
    """Read (requirement id, test id) pairs from a two-column .csv or .xlsx file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        tables = [_csv_rows(path)]
    elif suffix in {".xlsx", ".xlsm"}:
        tables = _xlsx_tables(path)
    else:
        raise ParseError(f"Unsupported ground-truth format '{suffix}'. Use .csv or .xlsx.")

    for rows in tables:
        if not rows:
            continue
        header = [_normalise(c) for c in rows[0]]
        req_col = next((i for i, h in enumerate(header) if h in _REQ_HEADERS), None)
        test_col = next((i for i, h in enumerate(header) if h in _TEST_HEADERS), None)
        if req_col is None or test_col is None:
            continue
        width = max(req_col, test_col)
        return {(row[req_col].strip(), row[test_col].strip()) for row in rows[1:]
                if len(row) > width and row[req_col].strip() and row[test_col].strip()}
    raise ParseError(f"{path.name} needs 'requirement_id' and 'test_id' columns.")


def _prf(predicted: set, actual: set) -> dict:
    hits = len(predicted & actual)
    precision = hits / len(predicted) if predicted else 0.0
    recall = hits / len(actual) if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"predicted": len(predicted), "actual": len(actual), "correct": hits,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def evaluate(result: TraceResult, truth: set[tuple[str, str]]) -> dict:
    """Compare predicted links and gaps with the labelled ones.

    match_accuracy is per test case: a labelled test counts as correct when its
    highest-scoring link is one of its labelled requirements, and a labelled orphan
    counts as correct when it is left unlinked.
    """
    req_ids = {r.id for r in result.requirements}
    test_ids = {t.id for t in result.tests}
    unknown = sorted({r for r, _ in truth} - req_ids) + sorted({t for _, t in truth} - test_ids)
    if unknown:
        raise ParseError(f"Ground truth refers to IDs missing from the inputs: {', '.join(unknown[:5])}")

    predicted = {(l.requirement_id, l.test_id) for l in result.links}
    truth_by_test: dict[str, set[str]] = {}
    for req_id, test_id in truth:
        truth_by_test.setdefault(test_id, set()).add(req_id)

    top_link: dict[str, str] = {}
    for link in sorted(result.links, key=lambda l: l.score):
        top_link[link.test_id] = link.requirement_id  # highest score written last

    correct = sum(
        1 for test_id in test_ids
        if (top_link.get(test_id) in truth_by_test[test_id] if test_id in truth_by_test
            else test_id not in top_link)
    )

    return {
        "match_accuracy": round(correct / len(test_ids), 4),
        "tests_correct": correct,
        "tests_total": len(test_ids),
        "links": _prf(predicted, truth),
        "uncovered_requirements": _prf(req_ids - {r for r, _ in predicted}, req_ids - {r for r, _ in truth}),
        "orphan_tests": _prf(test_ids - {t for _, t in predicted}, test_ids - set(truth_by_test)),
    }


def sweep(result: TraceResult, truth: set[tuple[str, str]], thresholds: list[float],
          margins: list[float]) -> list[dict]:
    """Evaluate a grid of threshold/margin settings, reusing the computed similarities."""
    rows = []
    for threshold in thresholds:
        for margin in margins:
            config = TraceConfig(threshold=threshold, margin=margin, top_k=result.config.top_k)
            scores = evaluate(result.with_config(config), truth)
            rows.append({
                "threshold": threshold,
                "margin": margin,
                "match_accuracy": scores["match_accuracy"],
                "link_f1": scores["links"]["f1"],
                "uncovered_f1": scores["uncovered_requirements"]["f1"],
                "orphan_f1": scores["orphan_tests"]["f1"],
            })
    return rows
