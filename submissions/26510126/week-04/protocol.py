"""The protocol layer: everything that happens to a message between the two
agents.

This is the layer FIPA specified and the layer the lecture says cannot check
what it needs to check. It reads the illocutionary force off a message and,
for a proposal, the price. What it costs to do that is the point of the
experiment, so every model call it makes goes through the meter.

One rule holds in all three conditions and it is worth stating because it is
easy to get wrong: a message the layer cannot read is still delivered to the
other agent, unchanged. The protocol layer is an observer here, not a filter.
If it dropped what it could not parse, the format conditions would differ in
what the agents saw and not only in what the harness could measure.
"""

import json
import re
from dataclasses import dataclass

import prompts

ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")
# Extension 2. FIPA's Communicative Act Library has 22; the lab took four and
# said so. These are the two the agents kept reaching for: query-ref is
# SC00037J 3.16, cfp is 3.4. Neither ends an episode and neither carries a
# price, so the episode loop needs no new branch for them.
ACTS_6 = ACTS + ("query-ref", "cfp")

_TAG = re.compile(r"^\s*\(\s*([a-zA-Z-]+)\s*\)")


@dataclass
class Reading:
    """What the protocol layer made of one message."""
    performative: str | None
    price: int | None
    ok: bool
    how: str          # for the log: which path produced this reading
    reader_calls: int = 0
    # Extension 1. The reader was asked and answered that it does not know.
    # Kept apart from ok=False, which means the layer could not read the
    # message at all. An abstention is a reading, and counting it as a format
    # error would hide the thing the extension exists to measure.
    unclear: bool = False


def _first_json_object(text: str):
    """The first balanced {...} in the text, parsed, or None.

    Needed because models do not reliably send a bare object even when the
    format paragraph says to. Two shapes show up: a fenced block, and an
    object followed by a sentence of prose. Both are read here rather than
    counted as format errors, because the JSON in them is well formed and
    the agent did name its act. What the prose after the object says is a
    separate matter and the log keeps the raw message so it stays visible.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value.replace(",", ""))
        if m:
            return int(m.group())
    return None


def read_structured(text: str, acts=ACTS) -> Reading:
    """A parser and no model call. This is the condition's whole claim."""
    obj = _first_json_object(text)
    if obj is None or not isinstance(obj, dict):
        return Reading(None, None, False, "no json object")
    how = "json" if text.strip().startswith("{") and text.strip().endswith("}") \
        else "json embedded in other text"
    act = obj.get("performative")
    if act not in acts:
        return Reading(None, None, False, f"{how}, performative={act!r} not an act")
    content = obj.get("content")
    price = _as_int(content.get("price")) if isinstance(content, dict) else None
    if act == "propose" and price is None:
        return Reading(act, None, False, f"{how}, propose with no integer price")
    return Reading(act, price, True, how)


def read_tagged(text: str, meter, chat, acts=ACTS) -> Reading:
    """A regex for the tag, and the reader only for a proposal's price.

    This is the middle of the trilemma: the act is fixed and free, the
    content is natural language and costs a call, but only on the messages
    where a number has to come out.
    """
    m = _TAG.match(text)
    if not m:
        return Reading(None, None, False, "no leading tag")
    act = m.group(1).lower()
    if act not in acts:
        return Reading(None, None, False,
                       f"tag ({act}) is not one of the {len(acts)} acts")
    if act != "propose":
        return Reading(act, None, True, "tag only, no reader call")
    raw = chat(prompts.PRICE_READER, [{"role": "user", "content": text}],
               meter, kind="reader")
    obj = _first_json_object(raw)
    price = _as_int(obj.get("price")) if isinstance(obj, dict) else None
    if price is None:
        return Reading(act, None, False, f"tag (propose), reader returned no price: {raw!r}",
                       reader_calls=1)
    return Reading(act, price, True, "tag + reader for price", reader_calls=1)


def read_free(history: list, meter, chat, acts=ACTS, abstain: bool = False) -> Reading:
    """The reader labels the last message, having seen the whole conversation.

    In the first run the reader is given the same four acts the agents have.
    It has no query-ref and no cfp, so a message that asks a question has to
    be forced into one of the four, and the prompt tells it to force one. What
    it did with that is data, not a defect, and the two extensions take the
    two ways out of it: let the reader abstain, or give it the missing acts.
    """
    transcript = "\n\n".join(f"{m['who']}: {m['text']}" for m in history)
    vocab = 6 if len(acts) == 6 else 4
    raw = chat(prompts.reader_prompt(vocab=vocab, abstain=abstain),
               [{"role": "user", "content": transcript}], meter, kind="reader")
    obj = _first_json_object(raw)
    if not isinstance(obj, dict):
        return Reading(None, None, False, f"reader returned no JSON: {raw!r}", reader_calls=1)
    act = obj.get("performative")
    if abstain and act == "unclear":
        return Reading(None, None, True, "reader: unclear", reader_calls=1, unclear=True)
    if act not in acts:
        return Reading(None, None, False, f"reader said performative={act!r}", reader_calls=1)
    price = _as_int(obj.get("price"))
    return Reading(act, price, True, f"reader: {act}" + (f" @ {price}" if price is not None else ""),
                   reader_calls=1)


def read(condition: str, text: str, history: list, meter, chat,
         acts=ACTS, abstain: bool = False) -> Reading:
    """Read one message under one condition. `history` ends with that message.

    The defaults reproduce the first run. `acts` widens the vocabulary
    (extension 2) and `abstain` lets the free reader decline (extension 1);
    abstention is a property of the reader, so it does nothing in the two
    conditions that have no reader deciding the act.
    """
    if condition == "structured":
        return read_structured(text, acts)
    if condition == "tagged":
        return read_tagged(text, meter, chat, acts)
    if condition == "free":
        return read_free(history, meter, chat, acts, abstain)
    raise ValueError(f"unknown condition {condition!r}")
