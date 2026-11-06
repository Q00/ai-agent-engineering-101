"""Week 04 — protocol layer: turns one raw agent message into
{"performative": ..., "price": ...} for each message format.
free/tagged call the reader LLM; structured is pure parsing, no model call
at all -- that difference is exactly what reader_calls measures.
"""
import json
import re

from model import call_model

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

READER_SYSTEM = (
    "You read one message from a price negotiation and label it. Reply "
    "with exactly one JSON object and nothing else: "
    '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
    '"price": <integer or null>}. price is the number being proposed or '
    "accepted, in the message's own units; null if no price is stated."
)

PRICE_ONLY_SYSTEM = (
    "Extract only the price number mentioned in this negotiation message. "
    'Reply with exactly one JSON object and nothing else: {"price": <integer or null>}.'
)

TAG_RX = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)")


def _parse_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _valid_price(price):
    return price is None or isinstance(price, (int, float))


def read_free(text, meter):
    """free: the reader LLM labels both performative and price."""
    data = _parse_json(call_model(READER_SYSTEM, text, meter))
    if data is None or data.get("performative") not in ACTS or not _valid_price(data.get("price")):
        return None
    return {"performative": data["performative"], "price": data.get("price")}


def read_tagged(text, meter):
    """tagged: regex for the tag; the reader LLM is only called for a price
    when the act actually carries one (propose/reject-proposal)."""
    m = TAG_RX.match(text)
    if not m:
        return None
    performative = m.group(1)
    if performative in ("accept-proposal", "refuse"):
        return {"performative": performative, "price": None}
    data = _parse_json(call_model(PRICE_ONLY_SYSTEM, text, meter))
    if data is None or not _valid_price(data.get("price")):
        return None
    return {"performative": performative, "price": data.get("price")}


def read_structured(text, meter):
    """structured: pure parsing, zero model calls."""
    data = _parse_json(text)
    if data is None or data.get("performative") not in ACTS:
        return None
    content = data.get("content") or {}
    if not isinstance(content, dict) or not _valid_price(content.get("price")):
        return None
    return {"performative": data["performative"], "price": content.get("price")}


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


def read_message(condition, text, meter):
    """Returns {"performative": ..., "price": ...}, or None on a format error
    (unparseable / missing tag / not valid JSON)."""
    return READERS[condition](text, meter)


if __name__ == "__main__":
    from model import Meter
    m = Meter()
    buyer_msg = ("Good morning, I appreciate you taking the time to meet with us today "
                 "regarding the regional edge data center construction project... "
                 "we'd like to open our discussion at 280 in budget units...")
    seller_msg = ("Good morning... I'd like to propose an initial figure of 420 units...")
    print("[free] buyer  ->", read_message("free", buyer_msg, m))
    print("[free] seller ->", read_message("free", seller_msg, m))
    print(f"tokens={m.tokens} calls={m.calls}")
