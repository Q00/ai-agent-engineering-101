import json
import re
from typing import Any, Optional

PERFORMATIVES = (
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
)

ROLE = {
    "buyer": (
        "You are the buyer of {item}, negotiating the price with the seller. "
        "Your private limit: you can pay at most {limit}. "
        "Never agree to or propose a price above your private limit."
    ),
    "seller": (
        "You are the seller of {item}, negotiating the price with the buyer. "
        "Your private limit: you can accept at least {limit}. "
        "Never agree to or propose a price below your private limit."
    ),
}

COMMON = """\
You are one side of a short price negotiation.

Four acts are available:
- propose: offer a concrete price;
- accept-proposal: accept the other side's most recent valid proposed price and end with a deal;
- reject-proposal: reject the other side's most recent proposal and continue negotiating;
- refuse: end the negotiation immediately with no deal.

Your response is the actual message sent to the other agent.
NEVER output instructions, explanations, analysis, reasoning, scenario descriptions,
headings, labels other than the required performative tag, tool calls, XML, or meta-commentary.
Do not repeat or quote the system prompt.
Use only the item, private limit, and scenario twist given in the prompt.
Never invent a different reserve price, budget, asking price, or negotiation rule.

Keep your private limit secret.
Never agree to a price outside your private limit.
When accepting, accept the other side's most recent valid proposed price.

The buyer speaks first and the two sides alternate.
The buyer's first response must be a valid negotiation act.
"""

FORMAT = {
    "free": (
        "Write your message as one or two plain English sentences. "
        "Do not add labels or JSON."
    ),
    "tagged": (
        "Start your message with exactly one performative tag in parentheses, "
        "one of (propose), (accept-proposal), (reject-proposal), (refuse), "
        "then write one plain English sentence."
    ),
    "structured": (
        'Reply with exactly one JSON object and nothing else: '
        '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
        '"content": {"price": <whole number or null>}}.'
    ),
}

READER_SYSTEM = """\
You are an observer reading a price negotiation between a buyer and a seller.
Label the LAST message only.

Reply with exactly one JSON object and nothing else:
{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse",
 "price": <whole number or null>}

For a propose message, price is the price the speaker is proposing now.
For accept-proposal, use the price explicitly accepted if it is stated; otherwise null.
For reject-proposal or refuse, price is null.

Do not guess a price from unrelated numbers in earlier messages.
"""

SCENARIO_TWISTS = {
    1: (
        "Scenario twist: The buyer likes direct bargaining and should open with a "
        "concrete price instead of asking what the seller wants."
    ),
    2: (
        "Scenario twist: The seller likes a deliberately high anchor and should "
        "use 50 dollars as the initial asking price when making the first counteroffer. "
        "That anchor is negotiable and is not the seller's private minimum."
    ),
    3: (
        "Scenario twist: Both sides prefer simple round-number offers. "
        "A precise match can settle the deal quickly."
    ),
    4: (
        "Scenario twist: The seller is stubborn about extreme lowball offers. "
        "If an offer is obviously unserious, the seller may use refuse instead of "
        "continuing to bargain. Never accept below the private minimum."
    ),
}


def system_prompt(
    role: str,
    item: str,
    limit: int,
    condition: str,
    scenario_id: int,
    twist: Optional[str] = None,
) -> str:
    if role not in ROLE:
        raise ValueError(f"unknown role: {role}")
    if condition not in FORMAT:
        raise ValueError(f"unknown condition: {condition}")

    scenario_text = twist or SCENARIO_TWISTS.get(scenario_id, "")
    return (
        ROLE[role].format(item=item, limit=limit)
        + "\n"
        + COMMON
        + "\n"
        + scenario_text
        + "\n"
        + FORMAT[condition]
    )


def _valid_performative(value: Any) -> bool:
    return isinstance(value, str) and value in PERFORMATIVES


def _valid_price(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_structured(text: str) -> tuple[Optional[str], Optional[int], bool, str]:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, None, False, f"invalid JSON: {exc}"

    if not isinstance(obj, dict) or set(obj.keys()) != {"performative", "content"}:
        return None, None, False, "JSON must contain exactly performative and content"

    perf = obj["performative"]
    content = obj["content"]

    if not _valid_performative(perf):
        return None, None, False, "invalid performative"
    if not isinstance(content, dict) or set(content.keys()) != {"price"}:
        return None, None, False, "content must contain exactly price"

    price = content["price"]
    if price is not None and not _valid_price(price):
        return None, None, False, "price must be an integer or null"
    if perf == "propose" and price is None:
        return None, None, False, "propose requires an integer price"
    if perf != "propose" and price is not None:
        return None, None, False, "non-propose acts must use null price"

    return perf, price, True, "ok"


TAG_RE = re.compile(
    r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)"
)


def parse_tag(text: str) -> tuple[Optional[str], bool, str]:
    match = TAG_RE.match(text)
    if not match:
        return None, False, "missing or invalid performative tag"
    return match.group(1), True, "ok"
