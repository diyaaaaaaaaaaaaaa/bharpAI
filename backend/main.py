"""
Entry point. Run this file to process the sample batch and watch the
agent's decisions print live. Writes two output files:
  - audit_log.jsonl  -- every decision, one line per event
  - results.json     -- final structured results, one entry per case,
                         used by api.py to serve the dashboard without
                         re-running any LLM calls

Run it with:  python main.py
"""
import json
import os
from collections import defaultdict
from dataclasses import asdict

from dotenv import load_dotenv
from google import genai

from models import Transaction, RecoveryCase
from agent import run_case
from run_baseline import naive_retry_baseline
from policy_engine import APPROVAL_THRESHOLD_AMOUNT
from generate_dataset import CAUSES

load_dotenv()

# The clean, named root causes generate_dataset.py actually draws from
# (excluding "ambiguous_error", which isn't a real gateway_response
# value -- it's the label for the deliberately-messy raw strings). Any
# gateway_response that isn't one of these known causes gets bucketed
# as "ambiguous_error" for reporting, same normalization
# generate_dataset.py's own sanity-check counter already does.
_KNOWN_CAUSES = {c for c, _ in CAUSES if c != "ambiguous_error"}


def _leakage_category(gateway_response: str) -> str:
    return gateway_response if gateway_response in _KNOWN_CAUSES else "ambiguous_error"


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Copy .env.example to .env "
            "and paste your real key into it."
        )

    client = genai.Client(api_key=api_key)

    data_path = os.path.join(os.path.dirname(__file__), "data", "sample_transactions.json")
    with open(data_path) as f:
        raw_transactions = json.load(f)

    results = []
    for raw in raw_transactions:
        txn = Transaction(**raw)
        case = RecoveryCase(transaction=txn)
        print(f"\n=== Processing {txn.transaction_id} ({txn.gateway_response}) ===")
        try:
            run_case(client, case)
            print(f"--- Final status: {case.status} ---")
        except Exception as e:
            # Every model in the fallback chain failed for this case.
            # Don't let one bad case take down the whole batch -- log
            # it honestly and move on. You'll want to know how many of
            # these happened when you report your real numbers.
            case.status = "error"
            print(f"--- FAILED after all retries/fallbacks: {e} ---")
        results.append(case)

    resolved = sum(1 for c in results if c.status == "resolved")
    escalated = sum(1 for c in results if c.status == "escalated")
    unresolved = sum(1 for c in results if c.status == "unresolved")
    errored = sum(1 for c in results if c.status == "error")
    total = len(results)

    # Guardrail activations -- every time the PolicyEngine blocked a
    # proposed action, across the whole batch. This is the PRD's §12
    # "guardrail activations" headline metric and the data source for
    # the dashboard's guardrail panel (§11).
    guardrail_activations = sum(len(c.guardrail_blocks) for c in results)
    cases_with_a_block = sum(1 for c in results if c.guardrail_blocks)

    # Approval-tiering activations -- every money-moving action flagged
    # because the transaction amount exceeded the policy threshold (see
    # policy_engine.APPROVAL_THRESHOLD_AMOUNT). Same pattern as the
    # guardrail count above, kept as its own metric since it's a
    # separate axis (allowed-but-flagged, not blocked).
    approval_activations = sum(len(c.approval_flags) for c in results)
    cases_requiring_approval = sum(1 for c in results if c.approval_flags)

    print("\n\n=== BATCH SUMMARY ===")
    print(f"Total cases:      {total}")
    print(f"Resolved:         {resolved} ({resolved/total:.0%})")
    print(f"Escalated:        {escalated} ({escalated/total:.0%})")
    print(f"Unresolved:       {unresolved} ({unresolved/total:.0%})")
    if errored:
        print(f"API errors:       {errored} ({errored/total:.0%}) -- these never got a real answer, investigate")
    print(f"Guardrail blocks: {guardrail_activations} (across {cases_with_a_block} case(s))")
    print(f"Approval flags:   {approval_activations} (across {cases_requiring_approval} case(s), amount > ₹{APPROVAL_THRESHOLD_AMOUNT:,})")

    # Baseline comparison, computed on the exact same batch, averaged
    # over several runs since it's a random simulation.
    baseline_runs = [naive_retry_baseline(raw_transactions) for _ in range(20)]
    baseline_avg = sum(baseline_runs) / len(baseline_runs)

    print(f"\nNaive retry-only baseline on same batch: {baseline_avg:.1f}/{total} ({baseline_avg/total:.0%})")
    print(f"RecoverAI resolved rate:                 {resolved}/{total} ({resolved/total:.0%})")

    # Money-at-risk / recovered totals -- useful headline numbers for
    # the pitch deck and dashboard command center.
    at_risk_amount = sum(r.transaction.amount for r in results)
    recovered_amount = sum(r.transaction.amount for r in results if r.status == "resolved")

    print(f"\n₹ at risk:     {at_risk_amount:,.2f}")
    print(f"₹ recovered:   {recovered_amount:,.2f} ({recovered_amount/at_risk_amount:.0%})")

    # Revenue leakage category breakdown -- same data you already have
    # per case, just grouped by root cause instead of left flat. Lets
    # the dashboard/pitch show *where* the money at risk actually
    # concentrates, not just the aggregate total.
    breakdown = defaultdict(lambda: {"total": 0, "resolved": 0, "amount_at_risk": 0.0, "amount_recovered": 0.0})
    for r in results:
        cat = _leakage_category(r.transaction.gateway_response)
        b = breakdown[cat]
        b["total"] += 1
        b["amount_at_risk"] += r.transaction.amount
        if r.status == "resolved":
            b["resolved"] += 1
            b["amount_recovered"] += r.transaction.amount

    leakage_breakdown = [
        {
            "category": cat,
            "total": vals["total"],
            "resolved": vals["resolved"],
            "recovery_rate": (vals["resolved"] / vals["total"]) if vals["total"] else 0.0,
            "amount_at_risk": round(vals["amount_at_risk"], 2),
            "amount_recovered": round(vals["amount_recovered"], 2),
        }
        for cat, vals in sorted(breakdown.items(), key=lambda kv: -kv[1]["amount_at_risk"])
    ]

    print("\nLeakage category breakdown:")
    for row in leakage_breakdown:
        print(
            f"  {row['category']:<20} {row['total']:>3} cases  "
            f"{row['resolved']:>3} resolved ({row['recovery_rate']:.0%})  "
            f"₹{row['amount_at_risk']:,.0f} at risk"
        )

    # Write structured results for the dashboard/API to read -- this
    # is what api.py serves, so the demo never needs to re-call the LLM.
    output = {
        "summary": {
            "total": total,
            "resolved": resolved,
            "escalated": escalated,
            "unresolved": unresolved,
            "errored": errored,
            "amount_at_risk": at_risk_amount,
            "amount_recovered": recovered_amount,
            "baseline_avg_resolved": baseline_avg,
            "guardrail_activations": guardrail_activations,
            "cases_with_a_guardrail_block": cases_with_a_block,
            "approval_activations": approval_activations,
            "cases_requiring_approval": cases_requiring_approval,
            "approval_threshold_amount": APPROVAL_THRESHOLD_AMOUNT,
            "leakage_breakdown": leakage_breakdown,
        },
        "cases": [
            {
                "transaction_id": r.transaction.transaction_id,
                "customer_id": r.transaction.customer_id,
                "amount": r.transaction.amount,
                "gateway_response": r.transaction.gateway_response,
                "status": r.status,
                "attempts_made": r.attempts_made,
                "messages_sent": r.messages_sent,
                "history": r.history,
                "guardrail_blocks": r.guardrail_blocks,
                "approval_flags": r.approval_flags,
            }
            for r in results
        ],
    }
    results_path = os.path.join(os.path.dirname(__file__), "results.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nWrote {results_path} for the dashboard/API to serve.")


if __name__ == "__main__":
    main()