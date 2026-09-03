"""
The PolicyEngine: deterministic guardrails.
These rules are NEVER decided by the LLM. This is the layer that
makes every money-adjacent action explainable, bounded and gated --
exactly what the buildathon rubric is grading you on. Keep this file
pure Python, no API calls, ever.
"""

MAX_RETRY_ATTEMPTS = 3
MAX_MESSAGES_PER_CASE = 1


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
