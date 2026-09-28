"""The protocol layer: what each condition tells the agents to write, and how
the harness reads what they wrote back into (performative, price).

Only two things differ between conditions, as the README fixes: the format
paragraph appended to both role prompts, and the reader below. Everything
else -- role prompts, scenarios, model, temperature, turn limit -- is shared.

    free        plain English         -> the LLM reader labels act and price
    tagged      "(act) plain English" -> regex for the act, LLM reader for a
                                         propose's price only
    structured  one JSON object       -> a parser, no model call

A message the layer cannot read is a format error. It is counted, logged, and
the raw text is still forwarded to the other agent: the other LLM may well
understand it, and whether it does is part of what the lab measures. It just
never changes the protocol state (no deal, no refusal, no binding price).
"""
import json
import re

from llm import Meter, chat, extract_json

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

# ---------------------------------------------------------------------------
# TODO(26512070): write these yourself. They are the independent variable.
#
# FORMATS[c] is appended verbatim to BOTH role prompts in condition c. It must
# name the four acts and say what each one does; it must NOT add negotiating
# advice, or the conditions stop differing in format only.
#
# READER_PROMPT is the system prompt of the reader model used by `free` (act +
# price) and by `tagged` (price only). It receives one message as the user
# turn and must answer with one JSON object:
#     {"performative": "<one of ACTS>", "price": <integer or null>}
# The README warns what happens when a buyer opens with a question and the
# reader must pick one of four acts: decide what you want the reader told, and
# report that choice -- do not quietly add a fifth act.
# ---------------------------------------------------------------------------
FORMATS = {
    "free": None,
    "tagged": None,
    "structured": None,
}
READER_PROMPT = None


def ready() -> list:
    """Names of the prompts still unwritten; the runner refuses a real run
    while this is non-empty."""
    missing = [f"FORMATS[{c!r}]" for c, v in FORMATS.items() if not v]
    return missing + ([] if READER_PROMPT else ["READER_PROMPT"])


def _price(value):
    """An integer price, or None. Accepts 250, 250.0, "250", "$250"."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    if isinstance(value, str):
        m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if m:
            return int(round(float(m.group().replace(",", ""))))
    return None


def _reply(performative=None, price=None, error=None, reader_calls=0, raw=None):
    return {"performative": performative, "price": price, "error": error,
            "reader_calls": reader_calls, "reader_raw": raw}


def _llm_read(text: str, meter: Meter, model, log):
    """One reader call. Returns (performative, price, raw_reply)."""
    raw = model(READER_PROMPT, [{"role": "user", "content": text}], meter, log)
    obj = extract_json(raw)
    if obj is None:
        return None, None, raw
    act = str(obj.get("performative", "")).strip().lower()
    return (act if act in ACTS else None), _price(obj.get("price")), raw


def _check(act, price, calls, raw):
    if act is None:
        return _reply(error="no valid performative", reader_calls=calls, raw=raw)
    if act == "propose" and price is None:
        return _reply(act, error="propose without a price", reader_calls=calls, raw=raw)
    return _reply(act, price if act == "propose" else None, reader_calls=calls, raw=raw)


def read_free(text, meter, model=chat, log=None):
    act, price, raw = _llm_read(text, meter, model, log)
    return _check(act, price, 1, raw)


TAG = re.compile(r"^\s*\(\s*(" + "|".join(ACTS) + r")\s*\)", re.I)


def read_tagged(text, meter, model=chat, log=None):
    m = TAG.match(text or "")
    if not m:
        return _reply(error="no leading (performative) tag")
    act = m.group(1).lower()
    if act != "propose":
        return _reply(act)
    # The tag carries the force; the price still lives in English, so the
    # reader is called for it -- and only its price is used.
    _, price, raw = _llm_read(text, meter, model, log)
    return _check(act, price, 1, raw)


def read_structured(text, meter=None, model=None, log=None):
    stripped = (text or "").strip()
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        obj = None
    salvaged = False
    if not isinstance(obj, dict):
        obj = extract_json(stripped)
        salvaged = obj is not None
    if obj is None:
        return _reply(error="not a JSON object")
    act = str(obj.get("performative", "")).strip().lower()
    content = obj.get("content")
    price = _price(content.get("price")) if isinstance(content, dict) else None
    out = _check(act if act in ACTS else None, price, 0, None)
    if salvaged and out["error"] is None:
        # Readable, but not "one JSON object": text around it or a code fence.
        # Kept as a deal-able message, flagged so the report can count it.
        out["note"] = "json salvaged from surrounding text"
    return out


READERS = {"free": read_free, "tagged": read_tagged, "structured": read_structured}


def read(condition, text, meter, model=chat, log=None):
    return READERS[condition](text, meter, model=model, log=log)
