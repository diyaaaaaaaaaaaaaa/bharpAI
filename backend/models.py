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
    # Cosmetic/display fields, not used in any diagnosis or policy
    # logic -- purely so the dashboard case list can show something
    # closer to a real ops console (customer name, payment method,
    # when it happened) instead of bare IDs.
    customer_name: str = ""
    payment_method: str = ""   # "UPI" | "Debit Card" | "Credit Card" |
                                # "Netbanking" | "Wallet" | "EMI"
    created_at: str = ""       # ISO 8601 string


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
    # Every money-moving action flagged as requiring elevated approval
    # because the transaction amount exceeded the policy threshold
    # (see policy_engine.APPROVAL_THRESHOLD_AMOUNT). Each entry is
    # {"action": ..., "input": ..., "amount": ...}. This is separate
    # from guardrail_blocks: an approval flag never blocks the action,
    # it documents that a human would have signed off in production.
    approval_flags: list = field(default_factory=list)