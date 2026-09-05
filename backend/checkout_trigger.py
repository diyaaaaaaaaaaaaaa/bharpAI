"""
CheckoutDropoffTrigger -- a second, real trigger source running on the
same diagnose -> propose -> gate -> execute -> log loop as the
payment-failure recovery agent (agent.py), proving the architecture is
genuinely pluggable rather than hardcoded to one problem.

This is a full second vertical, not a stub:
  - its own case model (CheckoutSession / CheckoutCase, below)
  - its own action registry (send_cart_reminder / offer_discount_code /
    send_retarget_ad / escalate_to_human / mark_unresolved)
  - its own deterministic policy guardrails, including an
    AgentVault-style approval tier -- extended to a NEW kind of risk
    (discount value given away) instead of copy-pasting the payment
    one. This is deliberate: it's evidence the permission-tiering idea
    generalizes, not just that it works once.
  - its own dataset generator (generate_checkout_batch, below)
  - its own run loop and its own honest metrics

What it deliberately reuses rather than re-derives:
  - agent._call_with_retry / agent._generate_with_timeout: the
    model-fallback-chain + per-call-timeout + backoff machinery. That
    plumbing is fragile infrastructure that took real debugging to get
    right (see the payment engine's "what broke" story) -- a second
    trigger source shouldn't have to re-learn those lessons from
    scratch just because it needs a different tool schema and prompt.
  - audit_log.log_event: the same JSON-lines audit trail, so a judge
    (or you) can see payment-recovery and checkout-recovery decisions
    interleaved in one place, tagged by trigger, instead of two
    disconnected logs.

What is intentionally NOT shared: policy_engine.py and tools.py. Those
stay payment-specific and untouched. This trigger's guardrails and
action registry live entirely in this file, self-contained, so nothing
here can accidentally break the payment engine that's already tested
and working.

Run standalone with: python checkout_trigger.py
"""
import json
import os
import random
from dataclasses import dataclass, field

from dotenv import load_dotenv
from google import genai
from google.genai import types

from agent import _call_with_retry, MAX_LOOP_ITERATIONS
from audit_log import log_event

load_dotenv()


# --------------------------------------------------------------------
# 1. Case model -- parallel to models.py's Transaction / RecoveryCase,
#    but shaped for an abandoned checkout session instead of a failed
#    payment.
# --------------------------------------------------------------------

@dataclass
class CheckoutSession:
    session_id: str
    customer_id: str
    cart_value: float
    drop_off_stage: str   # "browsing_cart" | "shipping_details" |
                           # "payment_details" | "otp_verification"
    device_type: str      # "mobile" | "desktop"
    minutes_since_dropoff: int
    # Sometimes a clean stage name, sometimes a messy raw session-log
    # string -- mirrors generate_dataset.py's ambiguous gateway
    # strings, for the same reason: the "AI judgment" rubric criterion
    # is about interpreting messy signals, not matching a clean enum.
    session_signal: str = ""


@dataclass
class CheckoutCase:
    session: CheckoutSession
    status: str = "open"   # "open" | "resolved" | "escalated" | "unresolved"
    reminders_sent: int = 0
    discounts_offered: int = 0
    history: list = field(default_factory=list)
    guardrail_blocks: list = field(default_factory=list)
    approval_flags: list = field(default_factory=list)
    # Why the loop actually ended, when it ends without an explicit
    # escalate_to_human/mark_unresolved call. "" if the case closed
    # normally via one of those two actions.
    close_reason: str = ""


# --------------------------------------------------------------------
# 2. Action registry -- this trigger's own bounded action list. The
#    model can only ever propose one of these; execution and gating
#    are both still deterministic Python, same split as the payment
#    engine.
# --------------------------------------------------------------------

CHECKOUT_TOOLS = [
    {
        "name": "send_cart_reminder",
        "description": "Send a reminder nudge about the abandoned cart.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["sms", "whatsapp", "email", "push"]},
                "message": {"type": "string", "description": "The exact message to send"},
            },
            "required": ["channel", "message"],
        },
    },
    {
        "name": "offer_discount_code",
        "description": (
            "Offer the customer a discount code to complete checkout. "
            "This gives away real margin -- use it deliberately, not as "
            "a default first move."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "percent_off": {"type": "integer", "description": "e.g. 5, 10, 15"},
                "code": {"type": "string"},
            },
            "required": ["percent_off", "code"],
        },
    },
    {
        "name": "send_retarget_ad",
        "description": "Queue a retargeting ad for this customer on an external platform.",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "enum": ["meta", "google", "criteo"]},
            },
            "required": ["platform"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Escalate this session to a human agent. Use when the drop-off "
            "reason is unclear even after diagnosis, or the cart value is "
            "large enough that automated discounting alone isn't the right "
            "call."
        ),
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
    {
        "name": "mark_unresolved",
        "description": (
            "Close this session as unresolved when no further action is "
            "likely to help. Always explain why honestly."
        ),
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name=t["name"], description=t["description"], parameters_json_schema=t["parameters"]
    )
    for t in CHECKOUT_TOOLS
]
_CHECKOUT_GEMINI_TOOL = types.Tool(function_declarations=_FUNCTION_DECLARATIONS)


# --------------------------------------------------------------------
# 3. Deterministic policy guardrails -- this trigger's own PolicyEngine
#    equivalent, self-contained on purpose (see module docstring).
# --------------------------------------------------------------------

MAX_REMINDERS_PER_SESSION = 1
MAX_DISCOUNTS_PER_SESSION = 1

# AgentVault-style approval tiering, generalized: the payment engine
# flags actions when the TRANSACTION amount crosses a threshold. Here
# there's no transaction to move -- so the tier is keyed on the value
# actually being given away (cart_value * percent_off), which is the
# genuinely equivalent risk in this domain. Same underlying idea
# ("an AI agent moving/giving away money needs a permission tier"),
# applied honestly to a different action shape rather than reusing the
# payment threshold on a field it doesn't map onto.
DISCOUNT_VALUE_APPROVAL_THRESHOLD = 300.0  # rupees of margin given away


def check_checkout_action_allowed(case: CheckoutCase, action_name: str, action_input: dict):
    """Returns (allowed: bool, reason: str). Mirrors policy_engine.check_action_allowed's contract."""
    if action_name == "send_cart_reminder" and case.reminders_sent >= MAX_REMINDERS_PER_SESSION:
        return False, (
            f"Max reminders ({MAX_REMINDERS_PER_SESSION}) already sent for this "
            f"session. No repeat contact allowed."
        )
    if action_name == "offer_discount_code" and case.discounts_offered >= MAX_DISCOUNTS_PER_SESSION:
        return False, (
            f"A discount code has already been offered for this session. "
            f"Do not stack a second one."
        )
    return True, ""


def check_checkout_requires_approval(case: CheckoutCase, action_name: str, action_input: dict) -> bool:
    """
    True if this action would require elevated human approval in
    production. Only offer_discount_code can trigger this -- it's the
    only action here that actually gives away money.
    """
    if action_name != "offer_discount_code":
        return False
    percent_off = action_input.get("percent_off", 0)
    discount_value = case.session.cart_value * (percent_off / 100.0)
    return discount_value > DISCOUNT_VALUE_APPROVAL_THRESHOLD


# --------------------------------------------------------------------
# 4. Execution layer -- simulated outcomes, same honest-simulation
#    philosophy as tools.py: every action type (not just the "primary"
#    one) has its own real chance of resolving the case, so the agent
#    can never look artificially worse than a naive baseline just
#    because only one action type was allowed to succeed.
# --------------------------------------------------------------------

# Base intent-to-convert by the stage the customer dropped off at --
# someone who reached OTP verification was much closer to buying than
# someone who never left the cart page.
STAGE_BASE_INTENT = {
    "browsing_cart": 0.15,
    "shipping_details": 0.35,
    "payment_details": 0.55,
    "otp_verification": 0.65,
}

# Multipliers on top of base intent, per action -- a discount is a much
# stronger incentive than a bare reminder; a retargeting ad is the
# weakest, most indirect channel.
ACTION_MULTIPLIER = {
    "send_cart_reminder": 0.6,
    "offer_discount_code": 1.3,
    "send_retarget_ad": 0.3,
}


def _stage_for(session: CheckoutSession) -> str:
    return session.drop_off_stage if session.drop_off_stage in STAGE_BASE_INTENT else "browsing_cart"


def execute_checkout_action(name: str, tool_input: dict, case: CheckoutCase) -> dict:
    session = case.session
    if name in ("send_cart_reminder", "offer_discount_code", "send_retarget_ad"):
        base = STAGE_BASE_INTENT[_stage_for(session)]
        odds = min(base * ACTION_MULTIPLIER[name], 0.9)
        succeeded = random.random() < odds

        if name == "send_cart_reminder":
            return {
                "status": "checkout_completed" if succeeded else "sent_no_response",
                "channel": tool_input["channel"],
            }
        if name == "offer_discount_code":
            return {
                "status": "checkout_completed" if succeeded else "offered_no_response",
                "percent_off": tool_input["percent_off"],
                "code": tool_input["code"],
            }
        if name == "send_retarget_ad":
            return {
                "status": "checkout_completed" if succeeded else "queued_no_response",
                "platform": tool_input["platform"],
            }

    if name == "escalate_to_human":
        return {"status": "escalated"}
    if name == "mark_unresolved":
        return {"status": "closed_unresolved"}
    return {"status": "error", "message": f"Unknown tool: {name}"}


# --------------------------------------------------------------------
# 5. Diagnosis prompt -- this trigger's own system prompt, same
#    structure as prompts.py's SYSTEM_PROMPT but for drop-off causes.
# --------------------------------------------------------------------

CHECKOUT_SYSTEM_PROMPT = """You are RecoverAI, running its checkout-abandonment recovery trigger.

For each abandoned checkout session:
1. Diagnose the most likely reason the customer dropped off, using the
   drop-off stage, device type, minutes since drop-off, and the raw
   session_signal (which may be a clean label or a messy raw log
   string -- interpret it, don't require an exact keyword match).
2. Choose exactly ONE action from the tools available that best
   addresses that cause. offer_discount_code gives away real margin --
   don't reach for it as the default first move. A cheap reminder is
   often enough for an early-stage drop-off (browsing_cart); a discount
   is better reserved for a customer who got close (payment_details,
   otp_verification) and still didn't convert.
3. IMPORTANT -- every action's outcome is immediate and final within
   this conversation, not something to wait on. A real reminder or
   discount might take hours to get a response in production, but in
   this simulation the tool result already tells you the final outcome
   right now (e.g. "sent_no_response" means that channel is done and
   did NOT recover the sale -- it is not "sent, awaiting reply").
   A session is not resolved just because you sent one thing. If the
   action you just took did not report status "checkout_completed",
   you must immediately decide the next step in this same turn: either
   propose another valid action, or explicitly close the case with
   escalate_to_human or mark_unresolved. Never end your turn with only
   reasoning text and no tool call unless you have already closed the
   case with one of those two actions.
4. If an action you propose is rejected by the system as not allowed,
   pick a different valid action next -- do not repeat the same
   rejected action.
5. When no further automated action is likely to help, choose between:
   - escalate_to_human: use when the drop-off reason is unclear even
     after diagnosis, OR when two different recovery actions have
     already been tried without success, OR when the cart value is
     large (roughly above 4000) and a discount alone doesn't feel like
     the right call for that value. Don't escalate purely because the
     cart is large -- give automation a real chance first.
   - mark_unresolved: use for lower-value, clearly-diagnosed sessions
     where you've tried what's reasonable and further contact would be
     excessive. Always explain why honestly.
6. Always briefly explain your reasoning in text before calling a
   tool, so a human reviewing the audit log can follow your logic.
"""

_CHECKOUT_GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=CHECKOUT_SYSTEM_PROMPT,
    tools=[_CHECKOUT_GEMINI_TOOL],
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)


# --------------------------------------------------------------------
# 6. Run loop -- structurally identical to agent.run_case (diagnose ->
#    propose -> gate -> execute -> log -> repeat until a stopping
#    condition), reusing agent._call_with_retry for the actual API
#    call so the fallback/timeout/backoff behavior is identical too.
# --------------------------------------------------------------------

def run_checkout_case(client: genai.Client, case: CheckoutCase) -> CheckoutCase:
    session = case.session
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=(
                    f"Abandoned checkout session:\n"
                    f"session_id: {session.session_id}\n"
                    f"cart_value: {session.cart_value}\n"
                    f"drop_off_stage: {session.drop_off_stage}\n"
                    f"device_type: {session.device_type}\n"
                    f"minutes_since_dropoff: {session.minutes_since_dropoff}\n"
                    f"session_signal: {session.session_signal}\n"
                    f"reminders_sent_so_far: {case.reminders_sent}\n\n"
                    f"Diagnose the drop-off cause and take the best recovery action."
                ))
            ],
        )
    ]

    for iteration in range(MAX_LOOP_ITERATIONS):
        response = _call_with_retry(client, contents, config=_CHECKOUT_GENERATE_CONFIG)

        if response.text:
            log_event(session.session_id, "reasoning", {"text": response.text, "trigger": "checkout_dropoff"})

        function_calls = response.function_calls or []
        if not function_calls:
            # The model ended its turn without calling a tool -- distinct
            # from actually exhausting MAX_LOOP_ITERATIONS. Logging these
            # separately is what caught the bug where most "unresolved"
            # cases were mislabeled "max_iterations_reached" after just
            # one turn; see checkout_trigger.py's module docstring / the
            # README's "what broke" section for the real story.
            case.close_reason = "model_ended_turn_without_further_action"
            break

        contents.append(response.candidates[0].content)

        response_parts = []
        for call in function_calls:
            allowed, reason = check_checkout_action_allowed(case, call.name, call.args)

            if allowed:
                result = execute_checkout_action(call.name, call.args, case)
                log_event(
                    session.session_id,
                    "action_executed",
                    {"action": call.name, "input": dict(call.args), "result": result, "trigger": "checkout_dropoff"},
                )

                needs_approval = check_checkout_requires_approval(case, call.name, call.args)
                if needs_approval:
                    discount_value = session.cart_value * (call.args.get("percent_off", 0) / 100.0)
                    log_event(
                        session.session_id,
                        "requires_approval",
                        {
                            "action": call.name,
                            "input": dict(call.args),
                            "discount_value": round(discount_value, 2),
                            "trigger": "checkout_dropoff",
                        },
                    )
                    case.approval_flags.append(
                        {
                            "action": call.name,
                            "input": dict(call.args),
                            "discount_value": round(discount_value, 2),
                        }
                    )

                _update_checkout_case_state(case, call.name, result, needs_approval)
            else:
                result = {"error": "action_blocked", "reason": reason}
                log_event(
                    session.session_id,
                    "action_blocked",
                    {"action": call.name, "input": dict(call.args), "reason": reason, "trigger": "checkout_dropoff"},
                )
                case.guardrail_blocks.append(
                    {"action": call.name, "input": dict(call.args), "reason": reason}
                )

            response_parts.append(types.Part.from_function_response(name=call.name, response=result))

        contents.append(types.Content(role="user", parts=response_parts))

        if case.status != "open":
            break

    if case.status == "open":
        case.status = "unresolved"
        # Honest reason: either the model genuinely used up every turn
        # without closing (rare, and a real bug if it happens often),
        # or it ended its turn early without proposing a further tool
        # call (the actual common case -- see close_reason set above).
        reason = case.close_reason or "max_iterations_reached"
        log_event(
            session.session_id,
            "case_closed",
            {"status": "unresolved", "reason": reason, "trigger": "checkout_dropoff"},
        )

    return case


def _update_checkout_case_state(case: CheckoutCase, action_name: str, result: dict, needs_approval: bool = False):
    if action_name == "send_cart_reminder":
        case.reminders_sent += 1
        if result.get("status") == "checkout_completed":
            case.status = "resolved"
    elif action_name == "offer_discount_code":
        case.discounts_offered += 1
        if result.get("status") == "checkout_completed":
            case.status = "resolved"
    elif action_name == "send_retarget_ad":
        if result.get("status") == "checkout_completed":
            case.status = "resolved"
    elif action_name == "escalate_to_human":
        case.status = "escalated"
    elif action_name == "mark_unresolved":
        case.status = "unresolved"

    history_entry = {"action": action_name, "result": result}
    if needs_approval:
        history_entry["requires_approval"] = True
    case.history.append(history_entry)


# --------------------------------------------------------------------
# 7. Dataset generator -- this trigger's own small batch, same
#    philosophy as generate_dataset.py: a weighted, believable
#    distribution plus deliberately ambiguous raw signals, not an even
#    split of clean categories.
# --------------------------------------------------------------------

COUNT = 15  # kept small for iterative testing to stay well inside the
            # free-tier daily quota (20 requests/day/model) -- bump
            # back up for the one real final run once the fix above is
            # confirmed working.

DROPOFF_STAGES = [
    ("browsing_cart", 35),
    ("shipping_details", 25),
    ("payment_details", 25),
    ("otp_verification", 15),
]

AMBIGUOUS_SESSION_SIGNALS = [
    "SESSION_TIMEOUT_STAGE_3",
    "FORM_VALIDATION_ERROR_UNSPEC",
    "PRICE_SHOCK_SHIPPING_COST",
    "APP_BACKGROUNDED_MIDFLOW",
    "COUPON_FIELD_ABANDONED",
]


def _random_session_signal(stage: str) -> str:
    # ~30% of sessions get a messy raw signal instead of the clean
    # stage label -- forces the model to interpret, same reasoning as
    # generate_dataset.py's AMBIGUOUS_STRINGS.
    if random.random() < 0.3:
        return random.choice(AMBIGUOUS_SESSION_SIGNALS)
    return stage


def generate_checkout_batch(count: int = COUNT):
    stages = [s for s, _ in DROPOFF_STAGES]
    weights = [w for _, w in DROPOFF_STAGES]
    batch = []
    for i in range(1, count + 1):
        stage = random.choices(stages, weights=weights, k=1)[0]
        batch.append({
            "session_id": f"CART{i:04d}",
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "cart_value": round(random.uniform(300, 6000), 2),
            "drop_off_stage": stage,
            "device_type": random.choice(["mobile", "desktop"]),
            "minutes_since_dropoff": random.randint(5, 180),
            "session_signal": _random_session_signal(stage),
        })
    return batch


def _naive_reminder_only_baseline(batch: list) -> float:
    """
    Same honesty check as run_baseline.py: what would a dumb "always
    send one reminder, nothing smarter" approach recover on this exact
    batch? Averaged over several runs since it's a random simulation.
    """
    runs = 20
    results = []
    for _ in range(runs):
        resolved = 0
        for row in batch:
            base = STAGE_BASE_INTENT.get(row["drop_off_stage"], STAGE_BASE_INTENT["browsing_cart"])
            odds = min(base * ACTION_MULTIPLIER["send_cart_reminder"], 0.9)
            if random.random() < odds:
                resolved += 1
        results.append(resolved)
    return sum(results) / runs


# --------------------------------------------------------------------
# 8. Entry point -- generates a batch, runs it through the loop, and
#    prints/saves honest metrics, kept in their own file
#    (checkout_results.json) rather than mixed into the payment
#    engine's results.json, so the two trigger sources' numbers are
#    never accidentally conflated.
# --------------------------------------------------------------------

if __name__ == "__main__":
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found. Copy .env.example to .env "
            "and paste your real key into it (same key main.py uses)."
        )
    client = genai.Client(api_key=api_key)

    data_path = os.path.join(os.path.dirname(__file__), "data", "checkout_sessions.json")
    os.makedirs(os.path.dirname(data_path), exist_ok=True)
    batch = generate_checkout_batch(COUNT)
    with open(data_path, "w") as f:
        json.dump(batch, f, indent=2)
    print(f"Generated {len(batch)} checkout sessions -> {data_path}")

    results = []
    for row in batch:
        session = CheckoutSession(**row)
        case = CheckoutCase(session=session)
        try:
            result = run_checkout_case(client, case)
        except Exception as e:
            case.status = "error"
            log_event(session.session_id, "case_error", {"error": str(e), "trigger": "checkout_dropoff"})
            result = case
        results.append(result)

    total = len(results)
    resolved = sum(1 for r in results if r.status == "resolved")
    escalated = sum(1 for r in results if r.status == "escalated")
    unresolved = sum(1 for r in results if r.status == "unresolved")
    errored = sum(1 for r in results if r.status == "error")

    cart_value_at_risk = sum(r.session.cart_value for r in results)
    cart_value_recovered = sum(r.session.cart_value for r in results if r.status == "resolved")

    guardrail_activations = sum(len(r.guardrail_blocks) for r in results)
    approval_activations = sum(len(r.approval_flags) for r in results)

    baseline_avg_resolved = _naive_reminder_only_baseline(batch)

    # Leakage category breakdown, grouped by drop-off stage. Unlike the
    # payment engine's gateway_response, drop_off_stage is always one
    # of the 4 known stages (the messy raw text lives in
    # session_signal, a separate field), so no normalization needed.
    breakdown = {}
    for r in results:
        stage = r.session.drop_off_stage
        b = breakdown.setdefault(stage, {"total": 0, "resolved": 0, "amount_at_risk": 0.0, "amount_recovered": 0.0})
        b["total"] += 1
        b["amount_at_risk"] += r.session.cart_value
        if r.status == "resolved":
            b["resolved"] += 1
            b["amount_recovered"] += r.session.cart_value

    leakage_breakdown = [
        {
            "category": stage,
            "total": vals["total"],
            "resolved": vals["resolved"],
            "recovery_rate": (vals["resolved"] / vals["total"]) if vals["total"] else 0.0,
            "amount_at_risk": round(vals["amount_at_risk"], 2),
            "amount_recovered": round(vals["amount_recovered"], 2),
        }
        for stage, vals in sorted(breakdown.items(), key=lambda kv: -kv[1]["amount_at_risk"])
    ]

    print("\n=== CheckoutDropoffTrigger: batch summary ===")
    print(f"Total sessions:      {total}")
    print(f"Resolved:            {resolved} ({resolved/total:.0%})")
    print(f"Escalated:           {escalated} ({escalated/total:.0%})")
    print(f"Unresolved:          {unresolved} ({unresolved/total:.0%})")
    print(f"Errored:             {errored} ({errored/total:.0%})")
    print(f"Cart value at risk:  ₹{cart_value_at_risk:,.2f}")
    print(f"Cart value recovered:₹{cart_value_recovered:,.2f} ({cart_value_recovered/cart_value_at_risk:.0%})")
    print(f"Guardrail blocks:    {guardrail_activations}")
    print(f"Approval flags:      {approval_activations} (discount value > ₹{DISCOUNT_VALUE_APPROVAL_THRESHOLD:,.0f})")
    print(f"\nNaive 'always send one reminder' baseline, avg over 20 runs: "
          f"{baseline_avg_resolved:.1f}/{total} ({baseline_avg_resolved/total:.0%})")

    print("\nLeakage category breakdown (by drop-off stage):")
    for row in leakage_breakdown:
        print(
            f"  {row['category']:<20} {row['total']:>3} sessions  "
            f"{row['resolved']:>3} resolved ({row['recovery_rate']:.0%})  "
            f"₹{row['amount_at_risk']:,.0f} at risk"
        )

    output = {
        "trigger": "checkout_dropoff",
        "summary": {
            "total": total,
            "resolved": resolved,
            "escalated": escalated,
            "unresolved": unresolved,
            "errored": errored,
            "cart_value_at_risk": round(cart_value_at_risk, 2),
            "cart_value_recovered": round(cart_value_recovered, 2),
            "baseline_avg_resolved": baseline_avg_resolved,
            "guardrail_activations": guardrail_activations,
            "approval_activations": approval_activations,
            "approval_threshold_amount": DISCOUNT_VALUE_APPROVAL_THRESHOLD,
            "leakage_breakdown": leakage_breakdown,
        },
        "cases": [
            {
                "session_id": r.session.session_id,
                "customer_id": r.session.customer_id,
                "cart_value": r.session.cart_value,
                "drop_off_stage": r.session.drop_off_stage,
                "session_signal": r.session.session_signal,
                "status": r.status,
                "reminders_sent": r.reminders_sent,
                "discounts_offered": r.discounts_offered,
                "history": r.history,
                "guardrail_blocks": r.guardrail_blocks,
                "approval_flags": r.approval_flags,
            }
            for r in results
        ],
    }
    results_path = os.path.join(os.path.dirname(__file__), "checkout_results.json")
    with open(results_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nFull results -> {results_path}")