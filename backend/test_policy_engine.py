"""
Proves the guardrails actually work, independent of whether the LLM
ever happens to trigger them in a given batch run. This is arguably
better evidence for the buildathon rubric than a live example -- it
shows the mechanism is correct by construction, not by luck.

Run with: python test_policy_engine.py
"""
from models import Transaction, RecoveryCase
from policy_engine import (
    check_action_allowed,
    check_requires_approval,
    MAX_RETRY_ATTEMPTS,
    MAX_MESSAGES_PER_CASE,
    APPROVAL_THRESHOLD_AMOUNT,
)


def make_case(attempts_made=0, messages_sent=0, amount=1000.0):
    txn = Transaction("TEST001", "CUSTTEST", amount, "card_declined")
    case = RecoveryCase(transaction=txn)
    case.attempts_made = attempts_made
    case.messages_sent = messages_sent
    return case


def test_retry_allowed_below_limit():
    case = make_case(attempts_made=MAX_RETRY_ATTEMPTS - 1)
    allowed, reason = check_action_allowed(case, "retry_payment", {"delay_minutes": 5})
    assert allowed, f"Expected retry to be allowed just below the limit, got blocked: {reason}"


def test_retry_blocked_at_limit():
    case = make_case(attempts_made=MAX_RETRY_ATTEMPTS)
    allowed, reason = check_action_allowed(case, "retry_payment", {"delay_minutes": 5})
    assert not allowed, "Expected retry to be BLOCKED at the max attempts limit, but it was allowed"
    assert reason, "A blocked action must always come with a reason -- the audit trail depends on it"


def test_message_allowed_first_time():
    case = make_case(messages_sent=0)
    allowed, _ = check_action_allowed(case, "send_recovery_message", {"channel": "sms", "message": "hi"})
    assert allowed, "Expected the first message to be allowed"


def test_message_blocked_second_time():
    case = make_case(messages_sent=MAX_MESSAGES_PER_CASE)
    allowed, reason = check_action_allowed(case, "send_recovery_message", {"channel": "sms", "message": "hi"})
    assert not allowed, "Expected a second message to be BLOCKED, but it was allowed"
    assert reason


def test_escalation_never_gated():
    # Escalation should never be blocked -- it's the safety valve, not
    # a limited resource. If this ever fails, something is very wrong.
    case = make_case(attempts_made=99, messages_sent=99)
    allowed, _ = check_action_allowed(case, "escalate_to_human", {"reason": "test"})
    assert allowed, "escalate_to_human must never be blocked by policy"


# --- AgentVault-style approval tiering -------------------------------
# Independent of the allowed/blocked checks above: these prove the
# amount-based approval flag fires exactly at the threshold, without
# ever touching the LLM.

def test_high_amount_requires_approval():
    case = make_case(amount=APPROVAL_THRESHOLD_AMOUNT + 1)
    assert check_requires_approval(case, "retry_payment"), (
        "Expected a transaction above the approval threshold to be flagged "
        "for approval on a money-moving action"
    )


def test_low_amount_does_not_require_approval():
    case = make_case(amount=500.0)
    assert not check_requires_approval(case, "retry_payment"), (
        "Expected a transaction well below the approval threshold to NOT "
        "be flagged for approval"
    )


def test_amount_exactly_at_threshold_does_not_require_approval():
    # Threshold check is a strict ">" -- exactly at the limit should
    # NOT be flagged, only strictly above it. Pins down the boundary
    # so it can't silently drift to ">=" or "<" during a refactor.
    case = make_case(amount=APPROVAL_THRESHOLD_AMOUNT)
    assert not check_requires_approval(case, "retry_payment"), (
        "Expected an amount exactly at the threshold to NOT require approval "
        "(the check is a strict greater-than)"
    )


def test_non_money_moving_action_never_requires_approval():
    # escalate_to_human and mark_unresolved don't move money, so even
    # a huge transaction amount shouldn't flag them -- gating the
    # safety-valve actions would just add friction for no benefit.
    case = make_case(amount=APPROVAL_THRESHOLD_AMOUNT * 10)
    assert not check_requires_approval(case, "escalate_to_human"), (
        "Non-money-moving actions must never be flagged for approval, "
        "regardless of transaction amount"
    )


if __name__ == "__main__":
    tests = [
        test_retry_allowed_below_limit,
        test_retry_blocked_at_limit,
        test_message_allowed_first_time,
        test_message_blocked_second_time,
        test_escalation_never_gated,
        test_high_amount_requires_approval,
        test_low_amount_does_not_require_approval,
        test_amount_exactly_at_threshold_does_not_require_approval,
        test_non_money_moving_action_never_requires_approval,
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {test.__name__}: {e}")

    print(f"\n{len(tests) - failures}/{len(tests)} guardrail tests passed")
    if failures:
        raise SystemExit(1)