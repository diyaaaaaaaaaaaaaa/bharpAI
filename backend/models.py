"""
Data models for RecoverAI.
Plain dataclasses on purpose -- no ORM, no database yet. You want to
be able to print(case) and read it while you're learning how the
agent behaves, before you add any real persistence.
"""
from dataclasses import dataclass, field


@dataclass
class Transaction:
    transaction_id: str
    customer_id: str
    amount: float
    gateway_response: str  # e.g. "insufficient_funds", "card_declined",
                            # "network_timeout", "bank_server_error"


@dataclass
class RecoveryCase:
    transaction: Transaction
    status: str = "open"       # "open" | "resolved" | "escalated" | "unresolved"
    attempts_made: int = 0
    messages_sent: int = 0
    history: list = field(default_factory=list)
    # Every action the PolicyEngine blocked for this case -- each entry
    # is {"action": ..., "input": ..., "reason": ...}. This is the data
    # behind the PRD's "guardrail activations" metric (§12) and the
    # dashboard's guardrail panel (§11). Without this field there's no
    # way to prove the gating in policy_engine.py is doing anything --
    # it just silently disappears into the audit log text file.
    guardrail_blocks: list = field(default_factory=list)