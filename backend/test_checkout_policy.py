"""
Deterministic tests for checkout_trigger.py's policy guardrails --
same philosophy as test_policy_engine.py: prove the gating logic is
correct on its own, independent of whatever the LLM happens to do in
any given live batch run. No API key, no network call, no LLM needed
to run this file.

Run with: python test_checkout_policy.py
"""
from checkout_trigger import (
    CheckoutSession,
    CheckoutCase,
    check_checkout_action_allowed,
    check_checkout_requires_approval,
    MAX_REMINDERS_PER_SESSION,
    MAX_DISCOUNTS_PER_SESSION,
    DISCOUNT_VALUE_APPROVAL_THRESHOLD,
)


def make_case(cart_value=1000.0, reminders_sent=0, discounts_offered=0):
    session = CheckoutSession(
        session_id="TESTCART001",
        customer_id="CUSTTEST",
        cart_value=cart_value,
        drop_off_stage="payment_details",
        device_type="mobile",
        minutes_since_dropoff=30,
    )
    case = CheckoutCase(session=session)
    case.reminders_sent = reminders_sent
    case.discounts_offered = discounts_offered
    return case


# --- allowed/blocked (reminder + discount caps) -----------------------

def test_reminder_allowed_below_limit():
    case = make_case(reminders_sent=MAX_REMINDERS_PER_SESSION - 1)
    allowed, _ = check_checkout_action_allowed(case, "send_cart_reminder", {})
    assert allowed, "Expected a reminder below the cap to be allowed"


def test_reminder_blocked_at_limit():
    case = make_case(reminders_sent=MAX_REMINDERS_PER_SESSION)
    allowed, reason = check_checkout_action_allowed(case, "send_cart_reminder", {})
    assert not allowed, "Expected a reminder at the cap to be blocked"
    assert reason, "Expected a non-empty reason when blocking"


def test_discount_allowed_below_limit():
    case = make_case(discounts_offered=MAX_DISCOUNTS_PER_SESSION - 1)
    allowed, _ = check_checkout_action_allowed(case, "offer_discount_code", {})
    assert allowed, "Expected a discount below the cap to be allowed"


def test_discount_blocked_at_limit():
    case = make_case(discounts_offered=MAX_DISCOUNTS_PER_SESSION)
    allowed, reason = check_checkout_action_allowed(case, "offer_discount_code", {})
    assert not allowed, "Expected a second discount to be blocked"
    assert reason, "Expected a non-empty reason when blocking"


def test_escalation_never_gated():
    case = make_case(reminders_sent=99, discounts_offered=99)
    allowed, _ = check_checkout_action_allowed(case, "escalate_to_human", {"reason": "test"})
    assert allowed, "escalate_to_human must never be blocked by policy"


# --- approval tiering (discount-value based, not amount-based) --------

def test_high_value_discount_requires_approval():
    # cart_value * percent_off/100 must exceed the threshold
    case = make_case(cart_value=5000.0)
    requires = check_checkout_requires_approval(
        case, "offer_discount_code", {"percent_off": 10}  # 10% of 5000 = 500 > 300
    )
    assert requires, "Expected a high-value discount to require approval"


def test_low_value_discount_does_not_require_approval():
    case = make_case(cart_value=1000.0)
    requires = check_checkout_requires_approval(
        case, "offer_discount_code", {"percent_off": 5}  # 5% of 1000 = 50 < 300
    )
    assert not requires, "Expected a low-value discount to NOT require approval"


def test_discount_value_exactly_at_threshold_does_not_require_approval():
    # Strict ">" check, same boundary convention as policy_engine.py's
    # amount-based tier -- pins it down so it can't silently drift.
    case = make_case(cart_value=3000.0)
    percent_off = (DISCOUNT_VALUE_APPROVAL_THRESHOLD / 3000.0) * 100  # exactly 300 rupees
    requires = check_checkout_requires_approval(
        case, "offer_discount_code", {"percent_off": percent_off}
    )
    assert not requires, "Expected exactly-at-threshold to NOT require approval"


def test_reminder_never_requires_approval_regardless_of_cart_value():
    # Only offer_discount_code gives away money in this trigger -- a
    # reminder costs nothing, so it should never be flagged no matter
    # how large the cart is.
    case = make_case(cart_value=100000.0)
    requires = check_checkout_requires_approval(case, "send_cart_reminder", {})
    assert not requires, "send_cart_reminder must never require approval"


def test_retarget_ad_never_requires_approval():
    case = make_case(cart_value=100000.0)
    requires = check_checkout_requires_approval(case, "send_retarget_ad", {"platform": "meta"})
    assert not requires, "send_retarget_ad must never require approval"


if __name__ == "__main__":
    tests = [
        test_reminder_allowed_below_limit,
        test_reminder_blocked_at_limit,
        test_discount_allowed_below_limit,
        test_discount_blocked_at_limit,
        test_escalation_never_gated,
        test_high_value_discount_requires_approval,
        test_low_value_discount_does_not_require_approval,
        test_discount_value_exactly_at_threshold_does_not_require_approval,
        test_reminder_never_requires_approval_regardless_of_cart_value,
        test_retarget_ad_never_requires_approval,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} checkout guardrail tests passed")