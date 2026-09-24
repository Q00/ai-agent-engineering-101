"""Role prompts, format paragraphs, and the protocol layer that reads a
message back into (performative, price) for each of the three conditions.
"""
import json
import re
from dataclasses import dataclass

from model import call_model

PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")

# ---------------------------------------------------------------- prompts

BUYER_BASE = """You are a buyer negotiating the price of {item} with a seller.
Your private budget is {budget}: the most you are allowed to pay. Never agree
to pay more than {budget}. You do not know the seller's minimum price. Try to
get the lowest price you can. You speak first.

Each turn you must perform exactly one of these four communicative acts:
- propose: offer a specific numeric price.
- accept-proposal: agree to the seller's last proposed price. This ends the
  negotiation with a deal at that price.
- reject-proposal: decline the seller's last price and keep negotiating.
- refuse: walk away. This ends the negotiation with no deal.
"""

SELLER_BASE = """You are a seller negotiating the price of {item} with a buyer.
Your private reserve price is {reserve}: the least you are allowed to accept.
Never agree to sell for less than {reserve}. You do not know the buyer's
budget. Try to get the highest price you can.

Each turn you must perform exactly one of these four communicative acts:
- propose: offer a specific numeric price.
- accept-proposal: agree to the buyer's last proposed price. This ends the
  negotiation with a deal at that price.
- reject-proposal: decline the buyer's last price and keep negotiating.
- refuse: walk away. This ends the negotiation with no deal.
"""

FORMAT_PARAGRAPHS = {
    "free": """
Message format: write your turn as plain, natural English negotiation
dialogue, one or two sentences. Do not use any tags, labels, or JSON. State a
specific numeric price whenever you propose a price or accept one.""",

    "tagged": """
Message format: start your reply with exactly one performative tag in
parentheses -- (propose), (accept-proposal), (reject-proposal), or (refuse)
-- followed by one plain English sentence. Example: "(propose) I can offer
95 for it." State a specific numeric price whenever you propose or accept
one.""",

    "structured": """
Message format: reply with exactly one JSON object and nothing else, of the
form {"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse",
"content": {"price": <integer>|null}}. Use content.price for propose (the
price you are offering) and for accept-proposal (the price you are
accepting). Use content.price = null for reject-proposal and refuse. Do not
write any text outside the JSON object.""",
}


def build_system_prompt(role: str, scenario: dict, condition: str) -> str:
    base = BUYER_BASE if role == "buyer" else SELLER_BASE
    return base.format(**scenario) + FORMAT_PARAGRAPHS[condition]


# ---------------------------------------------------------------- reader

READER_SYSTEM = """You read one message from a price negotiation between a
buyer and a seller. Classify it as exactly one of these four communicative
acts:
- propose: a specific price is being offered.
- accept-proposal: the speaker agrees to the other side's last price,
  ending the deal.
- reject-proposal: the speaker declines a price but keeps negotiating.
- refuse: the speaker is leaving the negotiation, no deal.

Reply with exactly one JSON object and nothing else:
{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse",
"price": <integer>|null}
Use price only for propose and accept-proposal (the numeric price
involved); use null otherwise."""

READER_PRICE_SYSTEM = """Extract the single numeric price being proposed or
accepted in this negotiation message. Reply with exactly one JSON object and
nothing else: {"price": <integer>|null}"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str):
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


@dataclass
class Parsed:
    performative: str | None
    price: int | None
    format_error: bool
    reader_calls: int


def _read_full(text: str) -> Parsed:
    reply = call_model(READER_SYSTEM, [{"role": "user", "content": text}],
                        max_tokens=100)
    obj = _extract_json(reply)
    if not obj or obj.get("performative") not in PERFORMATIVES:
        return Parsed(None, None, True, 1)
    price = obj.get("price")
    price = int(price) if isinstance(price, (int, float)) else None
    return Parsed(obj["performative"], price, False, 1)


def _read_price(text: str) -> Parsed:
    reply = call_model(READER_PRICE_SYSTEM, [{"role": "user", "content": text}],
                        max_tokens=50)
    obj = _extract_json(reply)
    price = obj.get("price") if obj else None
    price = int(price) if isinstance(price, (int, float)) else None
    return price


_TAG_RE = re.compile(r"^\s*\(\s*(propose|accept-proposal|reject-proposal|refuse)\s*\)", re.IGNORECASE)


def parse_message(condition: str, text: str) -> Parsed:
    if condition == "free":
        return _read_full(text)

    if condition == "tagged":
        m = _TAG_RE.match(text)
        if not m:
            return Parsed(None, None, True, 0)
        performative = m.group(1).lower()
        if performative == "propose":
            # per spec, the reader is used only for the price inside a
            # propose; accept-proposal resolves to the protocol's own
            # last-propose state (see negotiation.py), not a fresh read
            price = _read_price(text)
            return Parsed(performative, price, False, 1)
        return Parsed(performative, None, False, 0)

    if condition == "structured":
        obj = _extract_json(text)
        if not obj or obj.get("performative") not in PERFORMATIVES:
            return Parsed(None, None, True, 0)
        content = obj.get("content") or {}
        price = content.get("price")
        price = int(price) if isinstance(price, (int, float)) else None
        return Parsed(obj["performative"], price, False, 0)

    raise ValueError(f"unknown condition {condition}")
