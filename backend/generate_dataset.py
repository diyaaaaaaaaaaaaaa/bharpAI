"""
Generates a synthetic batch of failed-payment transactions for
RecoverAI to process (PRD section 10). Run this whenever you want a
fresh batch, or tweak COUNT / CAUSES / AMBIGUOUS_STRINGS below to
reshape it. Re-running gives you a different random batch each time --
useful for checking your agent doesn't just get lucky on one dataset.

Run with: python generate_dataset.py
"""
import json
import random

COUNT = 70  # PRD target: 60-100 cases -- quality over quantity

# Weighted distribution of root causes. Weights don't need to sum to
# 100 -- random.choices normalizes them. This mix is meant to look
# like a real payments platform's failure breakdown, not an even split.
CAUSES = [
    ("insufficient_funds", 30),
    ("card_declined", 25),
    ("network_timeout", 20),
    ("bank_server_error", 15),
    ("ambiguous_error", 10),  # deliberately messy gateway text --
                              # tests the LLM's diagnosis, not lookup
]

# Realistic-looking but genuinely ambiguous raw gateway strings. These
# exist specifically so the agent has to interpret messy text instead
# of pattern-matching a clean category -- this is the "AI judgment"
# criterion in the buildathon rubric, and it's the hardest thing to
# fake convincingly, so don't skip it.
AMBIGUOUS_STRINGS = [
    "ERR_502_GW_TIMEOUT_RETRY_LATER",
    "PSP_DECLINE_CODE_51",
    "AUTH_FAILED_UNKNOWN_REASON",
    "ISSUER_RESPONSE_CODE_UNRECOGNIZED",
    "GATEWAY_5XX_INTERMITTENT",
]


def random_gateway_response(cause: str) -> str:
    if cause == "ambiguous_error":
        return random.choice(AMBIGUOUS_STRINGS)
    return cause


def generate_batch(count: int):
    causes = [c for c, _ in CAUSES]
    weights = [w for _, w in CAUSES]
    batch = []

    for i in range(1, count + 1):
        cause = random.choices(causes, weights=weights, k=1)[0]
        batch.append({
            "transaction_id": f"TXN{i:04d}",
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "amount": round(random.uniform(200, 8000), 2),
            "gateway_response": random_gateway_response(cause),
        })

    # Inject one deliberate cluster of the same root cause in a row --
    # simulates an issuer outage (the "payment degradation" pattern
    # from the PRD), rather than only isolated independent failures.
    cluster_start = random.randint(0, max(count - 8, 0))
    for i in range(cluster_start, min(cluster_start + 6, count)):
        batch[i]["gateway_response"] = "network_timeout"
        batch[i]["amount"] = round(random.uniform(500, 3000), 2)

    return batch


if __name__ == "__main__":
    batch = generate_batch(COUNT)
    with open("data/sample_transactions.json", "w") as f:
        json.dump(batch, f, indent=2)
    print(f"Generated {len(batch)} transactions -> data/sample_transactions.json")

    # Quick sanity check on the distribution you actually got
    from collections import Counter
    counts = Counter(
        t["gateway_response"] if t["gateway_response"] in dict(CAUSES)
        else "ambiguous_error"
        for t in batch
    )
    print("Distribution:", dict(counts))
