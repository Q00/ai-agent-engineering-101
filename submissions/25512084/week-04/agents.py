from model import Meter, call_model


ALLOWED_ACTS = "propose, accept-proposal, reject-proposal, refuse"

COMMON_RULES = """
You are participating in a two-agent price negotiation.

Only these four communicative acts are allowed:
propose, accept-proposal, reject-proposal, refuse.

Meanings:
- propose: offer a price.
- accept-proposal: accept the other side's most recent proposed price and end with a deal.
- reject-proposal: reject the current proposal but continue negotiating.
- refuse: leave the negotiation with no deal.

Never accept a deal that violates your private price limit.
Keep messages short.
"""

FORMAT_PARAGRAPHS = {
    "free": """
FORMAT:
Reply in plain natural English. Do not use explicit performative tags or JSON.
""",
    "tagged": """
FORMAT:
Start every message with exactly one performative in parentheses:
(propose), (accept-proposal), (reject-proposal), or (refuse).
After the tag, write a short natural-English message.
""",
    "structured": """
FORMAT:
Reply with exactly one JSON object and nothing else:
{"performative": "propose|accept-proposal|reject-proposal|refuse",
 "content": {"price": integer_or_null}}
For propose, price must be an integer.
For the other acts, price may be null.
""",
}


def system_prompt(role, item, private_limit, condition):
    if role == "buyer":
        role_text = f"""
You are the BUYER negotiating for {item}.
Your private budget is {private_limit}.
You must never agree to a price above your budget.
Try to reach a deal when possible.
"""
    elif role == "seller":
        role_text = f"""
You are the SELLER negotiating {item}.
Your private reserve price is {private_limit}.
You must never agree to a price below your reserve.
Try to reach a deal when possible.
"""
    else:
        raise ValueError(f"unknown role: {role}")

    return COMMON_RULES + role_text + FORMAT_PARAGRAPHS[condition]


def agent_message(
    role,
    item,
    private_limit,
    condition,
    conversation,
    meter: Meter,
    opening=False,
):
    system = system_prompt(role, item, private_limit, condition)

    if opening:
        instruction = (
            "You are the buyer and must open the negotiation now. "
            "Send your first negotiation message."
        )
    else:
        instruction = (
            "Continue the negotiation based on the conversation below. "
            "Send exactly one next negotiation message."
        )

    transcript = "\n".join(conversation) if conversation else "(no messages yet)"

    user = (
        f"{instruction}\n\n"
        f"Conversation so far:\n{transcript}"
    )

    return call_model(
        system=system,
        messages=[{"role": "user", "content": user}],
        meter=meter,
        temperature=0,
    )
