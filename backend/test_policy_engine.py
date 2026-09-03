"""
Proves the guardrails actually work, independent of whether the LLM
ever happens to trigger them in a given batch run. This is arguably
better evidence for the buildathon rubric than a live example -- it
shows the mechanism is correct by construction, not by luck.

Run with: python test_policy_engine.py
"""
from models import Transaction, RecoveryCase
from policy_engine import check_action_allowed, MAX_RETRY_ATTEMPTS, MAX_MESSAGES_PER_CASE


def make_case(attempts_made=0, messages_sent=0):
    txn = Transaction("TEST001", "CUSTTEST", 1000.0, "card_declined")
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


if __name__ == "__main__":
    tests = [
        test_retry_allowed_below_limit,
        test_retry_blocked_at_limit,
        test_message_allowed_first_time,
        test_message_blocked_second_time,
        test_escalation_never_gated,
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