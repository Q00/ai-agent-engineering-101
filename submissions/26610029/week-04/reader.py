"""Week 04 — the reader: turns one agent message into {performative, price}.

Three conditions read a message three different ways (README table):
  free       -> LLM reader labels performative AND price, every message.
  tagged     -> regex for the tag; LLM reader only for the price of a propose.
  structured -> plain JSON parse, no model call at all.

Returns None on anything unparseable (unknown tag, broken JSON, reader reply
that isn't valid JSON) -- the caller counts that as a format_error and moves
on; it never crashes the episode.
"""
import json
import re

from tools_shared import create_message, MODEL, TEMPERATURE

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

READ_SYSTEM = (
    "You read a price negotiation between a buyer and a seller. You will be "
    "shown the conversation so far, ending with the LAST message. Label the "
    "LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose"|"accept-proposal"|"reject-proposal"|"refuse", '
    '"price": <whole number or null>}. '
    "price is the number of currency units proposed or being responded to, "
    "or null if the message states no number.")

PRICE_ONLY_SYSTEM = (
    "You read one message that has already been tagged (propose) in a price "
    "negotiation. Extract only the numeric price it proposes. Reply with "
    "exactly one JSON object and nothing else: {\"price\": <whole number or null>}.")


class ReaderStats:
    def __init__(self):
        self.calls = 0


def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _call_reader(system: str, messages: list, meter, stats: ReaderStats):
    stats.calls += 1
    resp = create_message(
        model=MODEL, max_tokens=200, system=system, messages=messages,
        extra_body={"temperature": TEMPERATURE})
    meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _extract_json(text)


def read_message(text: str, condition: str, meter, stats: ReaderStats, history=None):
    if condition == "structured":
        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            return None
        performative = parsed.get("performative")
        price = (parsed.get("content") or {}).get("price")
        if performative not in ACTS:
            return None
        if performative == "propose" and price is None:
            return None  # propose must carry a price to count as parsed
        return {"performative": performative, "price": price}

    if condition == "tagged":
        m = re.match(r"^\((propose|accept-proposal|reject-proposal|refuse)\)", text.strip())
        if not m:
            return None
        performative = m.group(1)
        if performative != "propose":
            return {"performative": performative, "price": None}
        parsed = _call_reader(PRICE_ONLY_SYSTEM, [{"role": "user", "content": text}], meter, stats)
        price = parsed.get("price") if isinstance(parsed, dict) else None
        return {"performative": performative, "price": price}

    # free: reader sees the conversation so far (history + this message),
    # labels the last message only
    convo = "\n".join(history or []) + ("\n" if history else "") + f"[last message] {text}"
    parsed = _call_reader(READ_SYSTEM, [{"role": "user", "content": convo}], meter, stats)
    if not isinstance(parsed, dict) or parsed.get("performative") not in ACTS:
        return None
    return {"performative": parsed["performative"], "price": parsed.get("price")}
