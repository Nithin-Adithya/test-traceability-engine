"""Streamlit front end for reviewers. Talks to the FastAPI service.

    streamlit run ui/app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("TRACE_API_URL", "http://localhost:8000").rstrip("/")
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
TIMEOUT = httpx.Timeout(600.0, connect=5.0)

st.set_page_config(page_title="Test Traceability Engine", layout="wide")
st.title("Test Traceability Engine")
st.caption("Upload a requirements document and your test cases to see which requirements are covered, "
           "which are not, and which tests trace to nothing.")

with st.sidebar:
    st.header("Matching settings")
    threshold = st.slider("Similarity threshold", 0.0, 1.0, 0.35, 0.01,
                          help="A test is linked to its best-matching requirement only if the similarity "
                               "reaches this value. Raise it for fewer, more certain links.")
    margin = st.slider("Runner-up margin", 0.0, 0.2, 0.0, 0.01,
                       help="Also link a test to other requirements scoring within this distance of its "
                            "best match. Leave at 0 to link each test to one requirement.")
    st.divider()
    st.caption(f"Service: {API_URL}")

sample_available = (SAMPLE_DIR / "requirements.docx").exists()
use_sample = st.toggle("Use the bundled sample data (300 requirements)", value=False,
                       disabled=not sample_available)

if use_sample:
    requirements_file = tests_files = truth_file = None
else:
    left, middle, right = st.columns(3)
    requirements_file = left.file_uploader("Requirements document", type=["docx", "xlsx", "csv"])
    tests_files = middle.file_uploader("Test cases", type=["xlsx", "csv", "docx", "json", "py"],
                                       accept_multiple_files=True)
    truth_file = right.file_uploader("Known links (optional)", type=["csv", "xlsx"],
                                     help="Two columns, requirement_id and test_id. "
                                          "If supplied, the results are scored against it.")


def _payload() -> list[tuple[str, tuple[str, bytes]]]:
    if use_sample:
        names = [("requirements", "requirements.docx"), ("tests", "test_cases.xlsx"),
                 ("ground_truth", "ground_truth.csv")]
        return [(field, (name, (SAMPLE_DIR / name).read_bytes())) for field, name in names]
    files = [("requirements", (requirements_file.name, requirements_file.getvalue()))]
    files += [("tests", (f.name, f.getvalue())) for f in tests_files]
    if truth_file:
        files.append(("ground_truth", (truth_file.name, truth_file.getvalue())))
    return files


ready = use_sample or (requirements_file and tests_files)
if st.button("Build traceability matrix", type="primary", disabled=not ready):
    try:
        with st.spinner("Matching tests to requirements..."):
            response = httpx.post(f"{API_URL}/trace", files=_payload(),
                                  data={"threshold": str(threshold), "margin": str(margin)}, timeout=TIMEOUT)
        if response.status_code == 422:
            st.error(response.json()["detail"])
        else:
            response.raise_for_status()
            st.session_state["result"] = response.json()
            report = httpx.get(f"{API_URL}/runs/{response.json()['run_id']}/report.xlsx", timeout=TIMEOUT)
            report.raise_for_status()
            st.session_state["report"] = report.content
    except httpx.ConnectError:
        st.error(f"Could not reach the traceability service at {API_URL}. "
                 "Start it with: uvicorn traceability.api:app --port 8000")
    except httpx.HTTPError as exc:
        st.error(f"The traceability service returned an error: {exc}")

result = st.session_state.get("result")
if result:
    summary = result["summary"]
    cols = st.columns(5)
    cols[0].metric("Requirements", summary["requirements"])
    cols[1].metric("Test cases", summary["tests"])
    cols[2].metric("Coverage", f"{summary['coverage_pct']}%")
    cols[3].metric("Uncovered requirements", summary["uncovered_requirements"])
    cols[4].metric("Orphan tests", summary["orphan_tests"])
    st.caption(f"Processed in {summary['timings_s']['request']} s.")

    st.download_button("Download Excel report", st.session_state["report"],
                       file_name="traceability_report.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    labels = ["Traceability matrix", "Uncovered requirements", "Orphan tests"]
    if result["evaluation"]:
        labels.append("Accuracy check")
    tabs = st.tabs(labels)

    with tabs[0]:
        matrix = pd.DataFrame(result["matrix"])
        only_gaps = st.checkbox("Show uncovered requirements only")
        if only_gaps:
            matrix = matrix[matrix["status"] == "Uncovered"]
        st.dataframe(matrix, hide_index=True, width="stretch")

    with tabs[1]:
        st.write("Requirements with no test above the similarity threshold. The closest test is shown "
                 "so you can judge whether it is a real gap or a missed match.")
        st.dataframe(pd.DataFrame(result["uncovered"]), hide_index=True, width="stretch")

    with tabs[2]:
        st.write("Tests that do not match any requirement strongly enough to be linked.")
        st.dataframe(pd.DataFrame(result["orphans"]), hide_index=True, width="stretch")

    if result["evaluation"]:
        with tabs[3]:
            scores = result["evaluation"]
            st.metric("Match accuracy", f"{scores['match_accuracy']:.1%}",
                      help="Share of test cases linked to a correct requirement, or correctly left unlinked.")
            rows = [{"check": label, **scores[key]} for label, key in
                    (("Links", "links"), ("Uncovered requirements", "uncovered_requirements"),
                     ("Orphan tests", "orphan_tests"))]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
