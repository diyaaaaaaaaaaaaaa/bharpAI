"""
Simple JSON-lines audit logger. Every decision the agent makes gets
written here so you (and later, a judge) can see exactly why the
agent did what it did, for any transaction, at any time.
"""
import json
import os
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(__file__), "audit_log.jsonl")


def log_event(case_id: str, event_type: str, details: dict):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "event_type": event_type,
        "details": details,
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"[AUDIT] {case_id} | {event_type}: {details}")
