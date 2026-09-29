"""Prompts and the protocol layer (how a message is read) for the three conditions.

The role paragraph and the act paragraph are the same in all three conditions; only FORMAT[condition]
is appended. The reader prompt does not depend on the condition.
"""
import json
import re

from llm import Meter, call_model

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. Never agree to a price above {limit}. "
              "Do not reveal your limit. Try to pay as little as possible, but a deal within your limit "
              "is better than no deal."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit}. Never agree to a price below {limit}. "
               "Do not reveal your limit. Try to get as much as possible, but a deal within your limit "
               "is better than no deal."),
}
COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to the other side's "
          "last price, which ends the negotiation with a deal), reject-proposal (decline the last price and "
          "keep negotiating), refuse (leave the negotiation for good, no deal). Each message performs exactly "
          "one of these acts. The negotiation ends after 8 messages in total.")
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (" Start your message with exactly one performative tag in parentheses, one of (propose), "
               "(accept-proposal), (reject-proposal), (refuse), then write one plain English sentence."),
    "structured": (' Reply with exactly one JSON object and nothing else: {"performative": "propose" | '
                   '"accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.'),
}


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. '
    "propose = the speaker offers a price; accept-proposal = the speaker agrees to the other side's last price; "
    "reject-proposal = the speaker declines the last price and keeps negotiating; refuse = the speaker leaves "
    "for good. price is the price the speaker of the LAST message offers, or null if it offers none.")
READER_PRICE_SYSTEM = (
    "You are an observer reading one message from a price negotiation. The message is a proposal. "
    'Reply with exactly one JSON object and nothing else: {"price": <whole number or null>}, '
    "the price the speaker offers, or null if it offers none.")

TAG_RE = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.I)
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*", re.I)


def _first_json(text: str):
    """Decode the first JSON object at the start of text. Returns (obj, trailing_text) or (None, text)."""
    s = FENCE_RE.sub("", text, count=1).lstrip()
    try:
        obj, end = json.JSONDecoder().raw_decode(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)          # the model's reply wraps the object: try the braces
        if not m:
            return None, text
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, text
        return obj, s[m.end():]
    rest = s[end:].strip()
    rest = re.sub(r"^```", "", rest).strip()
    return obj, rest


def _price(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\$?\s*\d+", v.strip()):
        return int(re.sub(r"\D", "", v))
    return None


def _reader(system: str, user: str, reader_meter: Meter):
    out = call_model(system, [{"role": "user", "content": user}], reader_meter)
    obj, _ = _first_json(out)
    return obj, out


def read(condition: str, text: str, transcript: list, reader_meter: Meter) -> dict:
    """Returns {perf, price, ok, raw, trailing}. ok=False counts as a format error."""
    r = {"perf": None, "price": None, "ok": False, "raw": "", "trailing": ""}
    if condition == "structured":                     # parser, no model call
        obj, rest = _first_json(text)
        r["trailing"] = rest
        if isinstance(obj, dict) and obj.get("performative") in ACTS:
            price = _price((obj.get("content") or {}).get("price")) if isinstance(obj.get("content"), dict) else None
            r.update(perf=obj["performative"], price=price, raw=json.dumps(obj))
            r["ok"] = not (r["perf"] == "propose" and price is None)
        else:
            r["raw"] = "unparsed"
        return r
    if condition == "tagged":                         # regex for the act, reader only for a propose's price
        m = TAG_RE.match(text)
        if not m:
            r["raw"] = "no tag"
            return r
        r["perf"] = m.group(1).lower()
        if r["perf"] != "propose":
            r["ok"], r["raw"] = True, f"tag={r['perf']}"
            return r
        obj, out = _reader(READER_PRICE_SYSTEM, text[m.end():].strip(), reader_meter)
        r["price"] = _price(obj.get("price")) if isinstance(obj, dict) else None
        r["ok"], r["raw"] = r["price"] is not None, f"tag=propose reader={out}"
        return r
    # free: the reader sees the whole conversation and labels the last message
    convo = "\n".join(f"[{who}] {msg}" for who, msg in transcript[:-1])
    who, last = transcript[-1]
    user = f"CONVERSATION SO FAR:\n{convo or '(none)'}\n\nLAST MESSAGE:\n[{who}] {last}"
    obj, out = _reader(READER_SYSTEM, user, reader_meter)
    r["raw"] = out
    if isinstance(obj, dict) and obj.get("performative") in ACTS:
        r["perf"], r["price"] = obj["performative"], _price(obj.get("price"))
        r["ok"] = not (r["perf"] == "propose" and r["price"] is None)
    return r
