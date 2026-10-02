from __future__ import annotations

import argparse
import json
import sys
from time import perf_counter

from traceability.embedder import DEFAULT_MODEL, get_embedder
from traceability.engine import TraceConfig, TraceabilityEngine
from traceability.evaluate import evaluate, load_ground_truth, sweep
from traceability.parsers import ParseError, load_requirements, load_tests
from traceability.report import write_report

_DEFAULTS = TraceConfig()


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("requirements", help="requirements document (.docx, .xlsx, .csv)")
    parser.add_argument("tests", help="test-case file or directory (.xlsx, .csv, .docx, .json, .py)")
    parser.add_argument("--threshold", type=float, default=_DEFAULTS.threshold,
                        help=f"minimum cosine similarity for a link (default {_DEFAULTS.threshold})")
    parser.add_argument("--margin", type=float, default=_DEFAULTS.margin,
                        help=f"also link runner-up requirements within this of the best (default {_DEFAULTS.margin})")
    parser.add_argument("--top-k", type=int, default=_DEFAULTS.top_k,
                        help=f"requirement candidates considered per test (default {_DEFAULTS.top_k})")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="sentence-transformers model name")
    parser.add_argument("-o", "--output", help="write the matrix and gap report to this .xlsx file")
    parser.add_argument("--json", dest="json_path", help="write the full result to this .json file")


def _run(args: argparse.Namespace):
    started = perf_counter()
    requirements = load_requirements(args.requirements)
    tests = load_tests(args.tests)
    config = TraceConfig(threshold=args.threshold, margin=args.margin, top_k=args.top_k)
    result = TraceabilityEngine(get_embedder(args.model), config).trace(requirements, tests)
    return result, perf_counter() - started


def _print_summary(result, wall: float) -> None:
    s = result.summary()
    print(f"Requirements: {s['requirements']}   Test cases: {s['tests']}   Links: {s['links']}")
    print(f"Covered: {s['covered_requirements']} ({s['coverage_pct']}%)   "
          f"Uncovered: {s['uncovered_requirements']}   Orphan tests: {s['orphan_tests']}")
    print(f"Time: {wall:.1f}s end to end (embedding {s['timings_s']['embedding']}s, "
          f"matching {s['timings_s']['matching']}s)")


def _write_outputs(args: argparse.Namespace, result, evaluation: dict | None = None) -> None:
    if args.output:
        print(f"Report written to {write_report(result, args.output, evaluation)}")
    if args.json_path:
        payload = result.to_dict() | ({"evaluation": evaluation} if evaluation else {})
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"JSON written to {args.json_path}")


def _trace(args: argparse.Namespace) -> None:
    result, wall = _run(args)
    _print_summary(result, wall)
    _write_outputs(args, result)


def _evaluate(args: argparse.Namespace) -> None:
    result, wall = _run(args)
    truth = load_ground_truth(args.ground_truth)
    scores = evaluate(result, truth)
    _print_summary(result, wall)
    print(f"\nMatch accuracy: {scores['match_accuracy']:.1%} "
          f"({scores['tests_correct']}/{scores['tests_total']} test cases)")
    for label, key in (("Links", "links"), ("Uncovered requirements", "uncovered_requirements"),
                       ("Orphan tests", "orphan_tests")):
        m = scores[key]
        print(f"{label}: predicted {m['predicted']}, actual {m['actual']}, correct {m['correct']}  "
              f"(precision {m['precision']:.1%}, recall {m['recall']:.1%}, F1 {m['f1']:.1%})")
    if args.sweep:
        thresholds = [round(0.30 + 0.05 * i, 2) for i in range(11)]
        print("\nthreshold  margin  match_acc  link_f1  uncovered_f1  orphan_f1")
        for row in sweep(result, truth, thresholds, [0.0, 0.02, 0.03, 0.05]):
            print(f"{row['threshold']:9.2f}  {row['margin']:6.2f}  {row['match_accuracy']:9.3f}  "
                  f"{row['link_f1']:7.3f}  {row['uncovered_f1']:12.3f}  {row['orphan_f1']:9.3f}")
    _write_outputs(args, result, scores)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="traceability", description="Map requirements to covering tests.")
    commands = parser.add_subparsers(dest="command", required=True)

    trace = commands.add_parser("trace", help="build the traceability matrix and gap report")
    _add_common(trace)
    trace.set_defaults(handler=_trace)

    score = commands.add_parser("evaluate", help="trace, then score against labelled pairs")
    _add_common(score)
    score.add_argument("ground_truth", help=".csv/.xlsx with requirement_id and test_id columns")
    score.add_argument("--sweep", action="store_true", help="also print a threshold/margin grid")
    score.set_defaults(handler=_evaluate)

    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (ParseError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
