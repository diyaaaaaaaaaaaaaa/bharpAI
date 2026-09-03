"""
Minimal API serving RecoverAI's results to a dashboard. Deliberately
does NOT call the LLM live -- it reads results.json, which main.py
writes after a full batch run. This keeps your dashboard demo fast,
free, and reproducible instead of depending on live API latency (and
quota) while a judge is watching.

Run with:  uvicorn api:app --reload
Then open: http://localhost:8000/docs  (auto-generated API explorer)
"""
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="RecoverAI API")

# Allows your React dashboard (running on a different port during
# development, e.g. localhost:5173) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # fine for a hackathon demo; tighten if this
                          # ever goes further than that
    allow_methods=["*"],
    allow_headers=["*"],
)

RESULTS_PATH = os.path.join(os.path.dirname(__file__), "results.json")


def _load_results():
    if not os.path.exists(RESULTS_PATH):
        raise HTTPException(
            status_code=404,
            detail="results.json not found -- run `python main.py` first to generate it.",
        )
    with open(RESULTS_PATH) as f:
        return json.load(f)


@app.get("/summary")
def get_summary():
    """Headline numbers for the dashboard's command center."""
    return _load_results()["summary"]


@app.get("/cases")
def get_cases(status: str | None = None):
    """
    All cases, optionally filtered by status
    (resolved / escalated / unresolved / error).
    """
    cases = _load_results()["cases"]
    if status:
        cases = [c for c in cases if c["status"] == status]
    return cases


@app.get("/cases/{transaction_id}")
def get_case_detail(transaction_id: str):
    """Full reasoning trail for one case -- what the dashboard's case-detail view shows."""
    cases = _load_results()["cases"]
    for c in cases:
        if c["transaction_id"] == transaction_id:
            return c
    raise HTTPException(status_code=404, detail=f"No case found with id {transaction_id}")


@app.get("/guardrails")
def get_guardrail_activations():
    """
    Every blocked action across the whole batch, flattened and tagged
    with the transaction_id it happened on. This is the "trust" proof
    the PRD's guardrail panel (§11) calls for -- a judge should be
    able to open this and see the PolicyEngine actually intervening,
    without digging through 70 nested case objects to find it.
    """
    cases = _load_results()["cases"]
    activations = []
    for c in cases:
        for block in c.get("guardrail_blocks", []):
            activations.append({"transaction_id": c["transaction_id"], **block})
    return activations