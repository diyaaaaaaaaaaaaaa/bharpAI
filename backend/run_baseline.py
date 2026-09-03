"""
The "dumb" baseline your PRD promises to compare against: no
diagnosis, no smarter alternate actions, just blindly retry every
failed payment once. This exists purely to prove your agent is
actually smarter, not just more complicated. Run it on the SAME batch
your agent processed for a fair comparison.

Run with: python run_baseline.py
"""
import json
import os

from tools import RETRY_SUCCESS_ODDS
import random


def naive_retry_baseline(transactions):
    resolved = 0
    for txn in transactions:
        odds = RETRY_SUCCESS_ODDS.get(txn["gateway_response"], 0.3)
        if random.random() < odds:
            resolved += 1
    return resolved


if __name__ == "__main__":
    data_path = os.path.join(os.path.dirname(__file__), "data", "sample_transactions.json")
    with open(data_path) as f:
        transactions = json.load(f)

    # Average over several runs since a single run of a random
    # simulation can get lucky/unlucky -- this is the honest way to
    # report it, not cherry-picking the best single run.
    runs = 20
    results = [naive_retry_baseline(transactions) for _ in range(runs)]
    avg_resolved = sum(results) / runs
    total = len(transactions)

    print(f"Naive 'always retry once' baseline over {runs} runs on {total} cases:")
    print(f"Average resolved: {avg_resolved:.1f} ({avg_resolved/total:.0%})")
    print(f"Range across runs: {min(results)}-{max(results)} resolved")
    print("\nCompare this to RecoverAI's actual BATCH SUMMARY from main.py")
    print("on the same data/sample_transactions.json file.")
