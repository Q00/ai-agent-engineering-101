"""Prompts and the protocol layer: how each condition's messages are written and read.

The role paragraph and the four-act paragraph are the same in all three
conditions. Only FORMAT[condition] is appended, and only read() differs in how
a message is turned into (performative, price).
"""
import json
import re
from dataclasses import dataclass
from typing import Optional

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
CONDITIONS = ("free", "tagged", "structured")

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit} dollars. Never agree to a price above {limit}. "
              "Do not reveal your limit. Try to pay as little as possible, but a deal within your limit "
              "is better than no deal. You speak first."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit} dollars. Never agree to a price below {limit}. "
               "Do not reveal your limit. Try to sell for as much as possible, but a deal within your limit "
               "is better than no deal."),
}
COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to the other side's "
          "last price, which ends the negotiation with a deal), reject-proposal (decline the last price and "
          "keep negotiating), refuse (leave the negotiation for good, no deal). Every message you send "
          "performs exactly one of these acts. Prices are whole numbers of dollars.")
FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (" Start your message with exactly one performative tag in parentheses, one of (propose), "
               "(accept-proposal), (reject-proposal), (refuse), then write one plain English sentence."),
    "structured": (' Reply with exactly one JSON object and nothing else: {"performative": "propose" | '
                   '"accept-proposal" | "reject-proposal" | "refuse", "content": {"price": <whole number or null>}}.'),
}
# The buyer's history is empty before it opens; chat templates need a user turn,
# so every condition gets this same fixed opening turn. It is not a seller message.
OPENING = "(The negotiation starts now. Send your first message to the seller.)"

READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", "price": <whole number or null>}. '
    "propose = the speaker offers a price; accept-proposal = the speaker agrees to the other side's last price, "
    "ending with a deal; reject-proposal = the speaker declines the last price and keeps negotiating; "
    "refuse = the speaker leaves the negotiation for good. price = the price the speaker of the last message "
    "offers or agrees to, as a whole number, or null if the message names no such price."
)

TAG_RE = re.compile(r"^\s*\(\s*(propose|accept-proposal|reject-proposal|refuse)\s*\)", re.IGNORECASE)
FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


@dataclass
class Reading:
    perf: Optional[str]
    price: Optional[int]
    ok: bool
    label: str             # what goes into the log
    trailing: bool = False  # structured: text after the JSON object


def _as_price(v) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _first_json(text: str):
    """(object, trailing_text) for the first JSON object in text, or (None, text)."""
    s = FENCE_RE.sub("", text.strip()).strip()
    start = s.find("{")
    if start < 0:
        return None, s
    try:
        obj, end = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError:
        return None, s
    return obj, (s[:start] + s[start + end:]).strip()


def reader(transcript: list, llm) -> dict:
    """One model call: label the last message of the transcript. Returns
    {"performative", "price"} or {} when the reply is not a usable label."""
    convo = "\n".join(f"[{who}] {text}" for who, text in transcript)
    raw = llm.chat(READER_SYSTEM, [{"role": "user", "content": convo}], kind="reader")
    obj, _ = _first_json(raw)
    if not isinstance(obj, dict):
        return {"raw": raw}
    perf = obj.get("performative")
    return {"performative": perf if perf in ACTS else None, "price": _as_price(obj.get("price")),
            **({} if perf in ACTS else {"raw": raw})}


def read(condition: str, text: str, transcript: list, llm) -> Reading:
    """Turn one message into (performative, price). ok=False means a format error."""
    if condition == "structured":
        obj, rest = _first_json(text)
        if not isinstance(obj, dict):
            return Reading(None, None, False, "parse error: no JSON object")
        perf = obj.get("performative")
        content = obj.get("content")
        price = _as_price(content.get("price")) if isinstance(content, dict) else None
        trailing = bool(rest)
        label = f"{{'performative': {perf!r}, 'price': {price!r}}}" + (f" trailing={rest[:80]!r}" if trailing else "")
        if perf not in ACTS:
            return Reading(None, None, False, "parse error: bad performative " + label, trailing)
        if perf == "propose" and price is None:
            return Reading(perf, None, False, "parse error: propose without price " + label, trailing)
        return Reading(perf, price, True, label, trailing)

    if condition == "tagged":
        m = TAG_RE.match(text)
        if not m:
            return Reading(None, None, False, "regex: no leading tag")
        perf = m.group(1).lower()
        if perf != "propose":
            return Reading(perf, None, True, f"regex: {perf}")
        lab = reader(transcript, llm)
        price = lab.get("price")
        label = f"regex: propose, reader: {lab}"
        if price is None:
            return Reading(perf, None, False, label + " (no price)")
        return Reading(perf, price, True, label)

    # free: the reader labels everything
    lab = reader(transcript, llm)
    perf, price = lab.get("performative"), lab.get("price")
    if perf is None:
        return Reading(None, None, False, f"reader: {lab}")
    if perf == "propose" and price is None:
        return Reading(perf, None, False, f"reader: {lab} (no price)")
    return Reading(perf, price, True, f"reader: {lab}")
