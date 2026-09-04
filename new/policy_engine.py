"""
The PolicyEngine: deterministic guardrails.
These rules are NEVER decided by the LLM. This is the layer that
makes every money-adjacent action explainable, bounded and gated --
exactly what the buildathon rubric is grading you on. Keep this file
pure Python, no API calls, ever.
"""

MAX_RETRY_ATTEMPTS = 3
MAX_MESSAGES_PER_CASE = 1

# AgentVault-style permission tiering. An AI agent moving money
# shouldn't have blanket authority -- any money-moving action on a
# transaction above this threshold gets flagged as `requires_approval`
# in addition to the normal allowed/blocked check. In this batch
# simulation the action still executes (there's no human-in-the-loop
# UI in the demo), but the flag is recorded on the case, logged to the
# audit trail, and surfaced on the dashboard as "would require human
# sign-off in production." This is a second, independent axis from
# allowed/blocked: an action can be allowed AND flagged for approval
# at the same time.
APPROVAL_THRESHOLD_AMOUNT = 5000

# Which actions actually move money (or offer a channel that could),
# and therefore need the amount-based approval check. Escalation and
# marking-unresolved never move money, so they're excluded on purpose
# -- gating them would just add friction to the one action that's
# supposed to be the safety valve.
MONEY_MOVING_ACTIONS = {"retry_payment", "offer_alt_method"}


def check_action_allowed(case, action_name: str, action_input: dict):
    """
    Returns (allowed: bool, reason: str).
    If not allowed, `reason` gets fed back to the model as the tool
    result, so it can pick a different valid action instead of
    silently failing or repeating the same blocked move.
    """
    if action_name == "retry_payment":
        if case.attempts_made >= MAX_RETRY_ATTEMPTS:
            return False, (
                f"Max retry attempts ({MAX_RETRY_ATTEMPTS}) already "
                f"reached for this case. Do not retry again."
            )

    if action_name == "send_recovery_message":
        if case.messages_sent >= MAX_MESSAGES_PER_CASE:
            return False, (
                "This customer has already been messaged once for this "
                "case. No repeat contact allowed."
            )

    return True, ""


def check_requires_approval(case, action_name: str) -> bool:
    """
    Returns True if this action, on this transaction, would require
    elevated human approval in production because the amount exceeds
    APPROVAL_THRESHOLD_AMOUNT. This is independent of allowed/blocked
    -- it never blocks anything itself, it just flags it. Only
    money-moving actions are checked; everything else always returns
    False.
    """
    if action_name not in MONEY_MOVING_ACTIONS:
        return False
    return case.transaction.amount > APPROVAL_THRESHOLD_AMOUNT