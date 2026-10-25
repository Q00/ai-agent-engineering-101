"""The protocol layer: turning a message into (performative, price).

This is the only module that knows which condition is running. negotiate.py
calls read() and gets the same three-tuple back whichever condition it is, so
the episode loop, the scoring and the logging never branch on the format.

One rule, identical in all three conditions:

    a message is read successfully when the layer gets one of the four acts
    AND, if that act is propose, a whole-number price.

Anything else is a format error. The message is still delivered to the other
side -- the agents keep talking, only the program loses track.
"""
import json
import re

from acl import READER_SYSTEM
from model import call_model

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

# The tag sits at the front. A little leading markdown is tolerated because
# models sometimes bold it; anything after the tag is the agent's sentence.
_TAG = re.compile(r"^[\s*_#>`-]*\((propose|accept-proposal|reject-proposal|refuse)\)",
                  re.IGNORECASE)


def _strip_fences(s: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", (s or "").strip(), flags=re.S).strip()


def _first_json_object(s: str):
    """The first {...} in the text, or None.

    Deliberately stops at the JSON object: a structured message that puts its
    real offer in a sentence after the JSON loses that offer here. That is the
    behaviour being measured, not a bug to patch.
    """
    t = _strip_fences(s)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _to_price(v):
    """int, 50.0, "50", "$50" and "50 dollars" all become 50. Otherwise None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v.replace(",", ""))
        if m:
            return int(m.group())
    return None


def _ok(perf, price) -> bool:
    return perf in ACTS and (price is not None or perf != "propose")


def _transcript_text(transcript) -> str:
    return "\n".join(f"{who}: {msg}" for who, msg in transcript)


def _ask_reader(transcript, meter, log):
    """One model call. Returns (performative, price); either may be None.

    READER_SYSTEM tells the reader it must pick one of the four acts even when
    the message fits none of them, so there is no unclear option to fall back
    on. When the reader answers with something unreadable anyway, that shows up
    here as (None, None) and the caller counts a format error.
    """
    raw = call_model(READER_SYSTEM, [{"role": "user", "content": _transcript_text(transcript)}],
                     meter, log=log)
    obj = _first_json_object(raw)
    if obj is None:
        log(f"    [reader] unreadable: {raw.strip()[:120]!r}")
        return None, None
    perf = obj.get("performative")
    perf = perf.strip().lower() if isinstance(perf, str) else None
    price = _to_price(obj.get("price"))
    log(f"    [reader] {{'performative': {perf!r}, 'price': {price!r}}}")
    return perf, price


def read_structured(text, log):
    """A parser, no model call."""
    obj = _first_json_object(text)
    if obj is None:
        log("    [parse] no JSON object")
        return None, None, False
    perf = obj.get("performative")
    perf = perf.strip().lower() if isinstance(perf, str) else None
    content = obj.get("content")
    price = _to_price(content.get("price")) if isinstance(content, dict) else None
    log(f"    [parse] {{'performative': {perf!r}, 'price': {price!r}}}")
    return perf, price, _ok(perf, price)


def read_tagged(text, transcript, meter, log):
    """Regex for the act; the reader only for the price inside a propose."""
    m = _TAG.match(text or "")
    if m is None:
        log("    [regex] no leading tag")
        return None, None, False
    perf = m.group(1).lower()
    log(f"    [regex] {perf!r}")
    if perf != "propose":
        return perf, None, True
    _, price = _ask_reader(transcript, meter, log)   # performative discarded
    return perf, price, _ok(perf, price)


def read_free(text, transcript, meter, log):
    """The reader labels every message."""
    perf, price = _ask_reader(transcript, meter, log)
    return perf, price, _ok(perf, price)


def read(condition, text, transcript, meter, log=print):
    """(performative, price, ok). `transcript` ends with the message being read."""
    if condition == "structured":
        return read_structured(text, log)
    if condition == "tagged":
        return read_tagged(text, transcript, meter, log)
    if condition == "free":
        return read_free(text, transcript, meter, log)
    raise ValueError(f"unknown condition: {condition}")
