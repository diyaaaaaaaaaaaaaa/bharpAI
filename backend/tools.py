"""
The Action Registry (PRD section 6).
This is the ONLY set of actions the agent is allowed to choose from.
`TOOLS` is the schema we hand to the Claude API so it knows what it
can call and with what arguments. `execute_tool` is the code that
ACTUALLY performs the action -- the model never touches this
directly. It only ever requests a call; your code decides whether to
run it (see policy_engine.py) and then runs it here.
"""
import random

TOOLS = [
    {
        "name": "retry_payment",
        "description": "Retry a failed payment after a cooldown period.",
        "parameters": {
            "type": "object",
            "properties": {
                "delay_minutes": {
                    "type": "integer",
                    "description": "Minutes to wait before retrying",
                }
            },
            "required": ["delay_minutes"],
        },
    },
    {
        "name": "send_recovery_message",
        "description": "Send a recovery nudge to the customer.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "enum": ["sms", "whatsapp", "email"],
                },
                "message": {
                    "type": "string",
                    "description": "The exact message to send",
                },
            },
            "required": ["channel", "message"],
        },
    },
    {
        "name": "offer_alt_method",
        "description": "Offer the customer an alternate payment method.",
        "parameters": {
            "type": "object",
            "properties": {
                "methods": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "e.g. ['upi', 'wallet', 'netbanking']",
                }
            },
            "required": ["methods"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Escalate this case to a human agent. Use when automated "
            "recovery has failed or the situation needs judgment you "
            "cannot safely apply."
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
            "Close this case as unresolved when no further action is "
            "safe or likely to help. Always be honest here -- do not "
            "mark a case resolved if it isn't."
        ),
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]

# Rough, made-up probabilities that a retry succeeds, per root cause.
# This is a placeholder for the MVP so you can see believable behavior
# today. On Day 2 you'll replace this with logic tied to your real
# synthetic dataset design.
RETRY_SUCCESS_ODDS = {
    "insufficient_funds": 0.35,
    "network_timeout": 0.70,
    "card_declined": 0.10,
    "bank_server_error": 0.60,
}

# Non-retry actions need their own simulated outcome too, or they can
# NEVER resolve a case -- which would make the agent look worse than a
# naive "always retry" baseline, even when avoiding a low-odds retry
# was the smarter call. These represent "did the customer act on it."
ALT_METHOD_SUCCESS_PROBABILITY = 0.45   # offered an alternative, customer used it
MESSAGE_SUCCESS_PROBABILITY = 0.30      # sent a nudge, customer responded and paid


def execute_tool(name: str, tool_input: dict, case) -> dict:
    """
    The real execution layer. In production this would call an actual
    payment gateway, SMS/WhatsApp API, etc. For the hackathon this
    simulates a realistic outcome so you can measure a genuine
    recovery rate instead of hardcoding success.
    """
    if name == "retry_payment":
        gateway_response = case.transaction.gateway_response
        odds = RETRY_SUCCESS_ODDS.get(gateway_response, 0.3)
        succeeded = random.random() < odds
        return {
            "status": "succeeded" if succeeded else "failed",
            "delay_minutes": tool_input["delay_minutes"],
        }

    if name == "send_recovery_message":
        succeeded = random.random() < MESSAGE_SUCCESS_PROBABILITY
        return {
            "status": "payment_completed" if succeeded else "sent_no_response",
            "channel": tool_input["channel"],
        }

    if name == "offer_alt_method":
        succeeded = random.random() < ALT_METHOD_SUCCESS_PROBABILITY
        return {
            "status": "payment_completed" if succeeded else "offered_no_response",
            "methods": tool_input["methods"],
        }

    if name == "escalate_to_human":
        return {"status": "escalated"}

    if name == "mark_unresolved":
        return {"status": "closed_unresolved"}

    return {"status": "error", "message": f"Unknown tool: {name}"}
