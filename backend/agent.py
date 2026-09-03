"""
The core agent loop (PRD section 5), running on Google's Gemini API
via the google-genai SDK.

Read this file top to bottom once it runs -- everything else in the
project just feeds into this loop: observe -> reason -> act ->
update -> repeat, until a stopping condition fires.
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from google import genai
from google.genai import types

from tools import TOOLS, execute_tool
from policy_engine import check_action_allowed
from prompts import SYSTEM_PROMPT
from models import RecoveryCase
from audit_log import log_event

# Using the "-latest" alias instead of a pinned version on purpose:
# Google has renamed/retired the underlying Flash model twice already
# during this build. The alias always points at their current
# recommended Flash model, so we stop chasing exact version strings.
# Confirmed present in your account via list_models.py output.
#
# We also fall back across a small chain of models if the primary one
# is overloaded (503) or rate-limited (429). Lite models generally get
# a much higher free-tier daily quota than the newest flagship model,
# so we lead with one instead of wasting a retry cycle on every single
# case discovering that gemini-flash-latest is already quota-capped.
MODEL_FALLBACK_CHAIN = [
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
]

MAX_LOOP_ITERATIONS = 5      # hard safety cap so a bug can never loop forever
MAX_RETRIES_PER_MODEL = 2    # how many times to retry each model before falling back
CALL_TIMEOUT_SECONDS = 45    # hard ceiling per API call -- see _generate_with_timeout

_executor = ThreadPoolExecutor(max_workers=4)

# Convert our provider-agnostic TOOLS list (tools.py) into the objects
# the Gemini SDK expects. If you ever swap providers, this is the only
# block that needs to change -- tools.py and policy_engine.py don't
# know or care which LLM is calling them.
_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name=t["name"],
        description=t["description"],
        parameters_json_schema=t["parameters"],
    )
    for t in TOOLS
]
_GEMINI_TOOL = types.Tool(function_declarations=_FUNCTION_DECLARATIONS)

_GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=[_GEMINI_TOOL],
    # We run the tool-call loop ourselves (below) so every proposed
    # action passes through the PolicyEngine before it's allowed to run.
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)


def _generate_with_timeout(client: genai.Client, model: str, contents):
    """
    Runs the API call in a background thread and gives up after
    CALL_TIMEOUT_SECONDS no matter what the SDK is doing internally.
    This is what stops a single hung request from blocking your whole
    batch for an hour, like it did once during this build -- the SDK's
    own internal retry logic can silently stack with ours otherwise.
    """
    future = _executor.submit(
        client.models.generate_content,
        model=model,
        contents=contents,
        config=_GENERATE_CONFIG,
    )
    return future.result(timeout=CALL_TIMEOUT_SECONDS)


def _call_with_retry(client: genai.Client, contents):
    """
    Free-tier models get overloaded (503 'high demand') and rate-limited
    (429) fairly often. This retries each model in MODEL_FALLBACK_CHAIN
    a couple of times with backoff, then moves to the next model before
    giving up entirely -- so a temporary spike on Google's side doesn't
    crash your whole batch run over one unlucky transaction.
    """
    last_error = None
    for model in MODEL_FALLBACK_CHAIN:
        for attempt in range(MAX_RETRIES_PER_MODEL):
            try:
                return _generate_with_timeout(client, model, contents)
            except FutureTimeoutError:
                last_error = TimeoutError(f"{model} did not respond within {CALL_TIMEOUT_SECONDS}s")
                print(f"[API] {model} timed out after {CALL_TIMEOUT_SECONDS}s; moving on...")
                break  # don't bother retrying the same model, go straight to fallback
            except Exception as e:
                last_error = e
                wait = 8 * (2 ** attempt)  # 8s, 16s per model
                print(f"[API] {model} failed ({e}); retrying in {wait}s...")
                time.sleep(wait)
        print(f"[API] giving up on {model} for now, trying next model in fallback chain...")
    # Every model in the chain failed -- raise so the caller can decide
    # what to do (see main.py, which now marks the case as errored
    # instead of crashing the whole batch).
    raise last_error


def run_case(client: genai.Client, case: RecoveryCase):
    txn = case.transaction
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=(
                    f"Failed transaction:\n"
                    f"transaction_id: {txn.transaction_id}\n"
                    f"amount: {txn.amount}\n"
                    f"gateway_response: {txn.gateway_response}\n"
                    f"attempts_so_far: {case.attempts_made}\n\n"
                    f"Diagnose the cause and take the best recovery action."
                ))
            ],
        )
    ]

    for _ in range(MAX_LOOP_ITERATIONS):
        response = _call_with_retry(client, contents)

        if response.text:
            log_event(txn.transaction_id, "reasoning", {"text": response.text})

        function_calls = response.function_calls or []
        if not function_calls:
            # Model responded without calling a tool -- nothing left to do.
            break

        # Gemini needs to see its own function_call turn reflected back
        # alongside your function_response turn on the next call.
        contents.append(response.candidates[0].content)

        response_parts = []
        for call in function_calls:
            allowed, reason = check_action_allowed(case, call.name, call.args)

            if allowed:
                result = execute_tool(call.name, call.args, case)
                log_event(
                    txn.transaction_id,
                    "action_executed",
                    {"action": call.name, "input": dict(call.args), "result": result},
                )
                _update_case_state(case, call.name, result)
            else:
                result = {"error": "action_blocked", "reason": reason}
                log_event(
                    txn.transaction_id,
                    "action_blocked",
                    {"action": call.name, "input": dict(call.args), "reason": reason},
                )
                # Record the block on the case itself, not just in the
                # log file -- this is what lets main.py surface a real
                # "guardrail activations" count and lets the dashboard
                # show a guardrail panel per case instead of nothing.
                case.guardrail_blocks.append(
                    {
                        "action": call.name,
                        "input": dict(call.args),
                        "reason": reason,
                    }
                )

            response_parts.append(
                types.Part.from_function_response(name=call.name, response=result)
            )

        contents.append(types.Content(role="user", parts=response_parts))

        if case.status != "open":
            break

    if case.status == "open":
        # Safety cap hit without a clean resolution -- always close
        # honestly instead of leaving it in limbo.
        case.status = "unresolved"
        log_event(
            txn.transaction_id,
            "case_closed",
            {"status": "unresolved", "reason": "max_iterations_reached"},
        )

    return case


def _update_case_state(case: RecoveryCase, action_name: str, result: dict):
    if action_name == "retry_payment":
        case.attempts_made += 1
        if result.get("status") == "succeeded":
            case.status = "resolved"
    elif action_name == "send_recovery_message":
        case.messages_sent += 1
        if result.get("status") == "payment_completed":
            case.status = "resolved"
    elif action_name == "offer_alt_method":
        if result.get("status") == "payment_completed":
            case.status = "resolved"
    elif action_name == "escalate_to_human":
        case.status = "escalated"
    elif action_name == "mark_unresolved":
        case.status = "unresolved"
    case.history.append({"action": action_name, "result": result})