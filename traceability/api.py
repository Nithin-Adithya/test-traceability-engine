"""FastAPI service exposing the traceability engine.

Run with:  uvicorn traceability.api:app --port 8000
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections import OrderedDict
from pathlib import Path
from time import perf_counter

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from traceability.embedder import DEFAULT_MODEL, Embedder, get_embedder
from traceability.engine import TraceConfig, TraceabilityEngine, TraceResult
from traceability.evaluate import evaluate, load_ground_truth
from traceability.parsers import ParseError, load_requirements, load_tests
from traceability.report import workbook_bytes

_DEFAULTS = TraceConfig()
_MAX_RUNS = 20
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(title="Test Traceability Engine", version="0.1.0")
_runs: OrderedDict[str, tuple[TraceResult, dict | None]] = OrderedDict()


def embedder_dependency() -> Embedder:
    return get_embedder(os.environ.get("TRACE_MODEL", DEFAULT_MODEL))


def _save(upload: UploadFile, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / Path(upload.filename or "upload").name
    target.write_bytes(upload.file.read())
    return target


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/trace")
def trace(
    requirements: UploadFile = File(..., description="Requirements document (.docx, .xlsx, .csv)"),
    tests: list[UploadFile] = File(..., description="Test-case files (.xlsx, .csv, .docx, .json, .py)"),
    ground_truth: UploadFile | None = File(None, description="Optional labelled pairs (.csv, .xlsx)"),
    threshold: float = Form(_DEFAULTS.threshold),
    margin: float = Form(_DEFAULTS.margin),
    top_k: int = Form(_DEFAULTS.top_k),
    embedder: Embedder = Depends(embedder_dependency),
) -> dict:
    started = perf_counter()
    try:
        config = TraceConfig(threshold=threshold, margin=margin, top_k=top_k)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parsed_requirements = load_requirements(_save(requirements, root / "requirements"))
            for upload in tests:
                _save(upload, root / "tests")
            parsed_tests = load_tests(root / "tests")
            result = TraceabilityEngine(embedder, config).trace(parsed_requirements, parsed_tests)
            evaluation = None
            if ground_truth is not None and ground_truth.filename:
                evaluation = evaluate(result, load_ground_truth(_save(ground_truth, root / "truth")))
    except (ParseError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    run_id = uuid.uuid4().hex
    _runs[run_id] = (result, evaluation)
    while len(_runs) > _MAX_RUNS:
        _runs.popitem(last=False)

    payload = result.to_dict()
    payload["run_id"] = run_id
    payload["evaluation"] = evaluation
    payload["summary"]["timings_s"]["request"] = round(perf_counter() - started, 3)
    return payload


def _get_run(run_id: str) -> tuple[TraceResult, dict | None]:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Unknown or expired run id.")
    return _runs[run_id]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    result, evaluation = _get_run(run_id)
    return result.to_dict() | {"run_id": run_id, "evaluation": evaluation}


@app.get("/runs/{run_id}/report.xlsx")
def get_report(run_id: str) -> Response:
    result, evaluation = _get_run(run_id)
    return Response(
        content=workbook_bytes(result, evaluation),
        media_type=_XLSX,
        headers={"Content-Disposition": 'attachment; filename="traceability_report.xlsx"'},
    )
