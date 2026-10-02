# Test Traceability Engine

Maps each requirement in a requirements document to the test cases that cover it, using
sentence embeddings and a FAISS similarity index, and reports the gaps: requirements with no
test and tests that trace to no requirement.

**Stack:** Python, sentence-transformers, FAISS, FastAPI, Streamlit.

## Setup

```bash
uv venv && uv pip install -e ".[dev]"
```

The first run downloads the embedding model (`all-mpnet-base-v2`, about 420 MB).

## Use

Command line:

```bash
.venv/bin/traceability trace data/sample/requirements.docx data/sample/test_cases.xlsx -o out/report.xlsx
```

API and reviewer UI (two terminals):

```bash
.venv/bin/uvicorn traceability.api:app --port 8000
```

```bash
.venv/bin/streamlit run ui/app.py
```

The UI is at http://localhost:8501 and the API docs at http://localhost:8000/docs.

| Endpoint | Purpose |
| --- | --- |
| `POST /trace` | Upload `requirements`, one or more `tests` files, optional `ground_truth`; returns matrix, links, gaps |
| `GET /runs/{id}` | Fetch a previous result (last 20 are kept in memory) |
| `GET /runs/{id}/report.xlsx` | Excel report: summary, matrix, links, uncovered requirements, orphan tests |
| `GET /health` | Liveness check |

## Input formats

- **Requirements:** `.docx` (tables with an ID column and a description column, or paragraphs
  like `REQ-001: The system shall ...`), `.xlsx`, `.csv`.
- **Tests:** `.xlsx`, `.csv`, `.docx`, `.json`, pytest `.py` files (function name plus docstring),
  or a directory mixing these. Title, steps and expected-result columns are combined.
- **Ground truth (optional):** `.csv`/`.xlsx` with `requirement_id` and `test_id` columns.

Column headers are matched loosely ("Req ID", "Requirement ID", "Test Case ID", "Expected Result", ...).

## How matching works

1. Requirements and tests are embedded and L2-normalised.
2. Each test is searched against a FAISS inner-product index of requirements (cosine similarity).
3. A test is linked to its best requirement if the score reaches `threshold` (default 0.35), and to
   runners-up within `margin` of the best (default 0, so one requirement per test).
4. Requirements with no link are **uncovered**; tests with no link are **orphans**. Each gap is
   reported with its closest candidate so a reviewer can tell a real gap from a missed match.

Set `TRACE_MODEL` (API) or `--model` (CLI) to use another sentence-transformers model, for
example `sentence-transformers/all-MiniLM-L6-v2` for a smaller download at lower accuracy.

## Measured results

On the bundled sample set (300 requirements, 466 test cases), default settings, Apple Silicon laptop:

| Metric | Result |
| --- | --- |
| End-to-end time, model cached | about 14 s from the CLI; 2.3 s per request once the API has the model loaded |
| Match accuracy (test linked to a correct requirement, or correctly left unlinked) | 82.6% (385/466) |
| Link precision / recall | 82.7% / 83.6% |
| Uncovered requirements | 30 flagged, 19 of the 27 real ones found |
| Orphan tests | 9 flagged, 7 of the 14 real ones found |

Reproduce with:

```bash
.venv/bin/traceability evaluate data/sample/requirements.docx data/sample/test_cases.xlsx data/sample/ground_truth.csv --sweep
```

Caveats on these numbers:

- The sample set is **synthetic**, produced by `scripts/generate_sample_data.py`. Its labels are
  known by construction, not hand-labelled, and its difficulty is set by the generator (a quarter of
  tests rename the business object, and all of them paraphrase the behaviour).
- The default threshold was chosen with `--sweep` on this same set, so the accuracy is optimistic
  for unseen data. Re-run the sweep on a labelled sample of your own documents.
- Gap detection is weaker than link accuracy: a test linked to the wrong requirement hides one gap
  and creates another. Treat the gap report as a review queue, not a verdict.

## Tests

```bash
.venv/bin/python -m pytest
```

Unit tests use a deterministic hashing embedder, so they need no model download.
