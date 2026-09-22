"""Message protocol of the contract net: the three message kinds, the
no-response record, the message bus that counts them, and the bid parser.

Counting rule (the `messages` metric): one Announcement per contractor, one
Bid whose participate flag is true, one Award per awarded task. A declined
bid, an unparseable reply and an API error are recorded on the bus as NoBid
but are not counted as messages: a node that did not answer sent nothing.
"""
import json
import re
from dataclasses import dataclass, field


@dataclass
class Announcement:
    task_id: int
    desc: str
    to: str                      # contractor name; a broadcast is one Announcement per contractor
    text: str = ""               # the announcement as sent


@dataclass
class Bid:
    contractor: str
    participate: bool
    confidence: float            # 0-100
    reason: str
    raw: str = ""
    normalized: bool = False     # True when confidence came back on a 0-1 or "%" scale


@dataclass
class NoBid:
    contractor: str
    tag: str                     # parse_fail | api_error
    raw: str = ""


@dataclass
class Award:
    task_id: int
    winner: str


@dataclass
class MessageBus:
    events: list = field(default_factory=list)

    def send(self, msg):
        self.events.append(msg)
        return msg

    @property
    def messages(self) -> int:
        """Announcements + participating bids + awards."""
        return sum(1 for e in self.events
                   if isinstance(e, (Announcement, Award))
                   or (isinstance(e, Bid) and e.participate))

    def count(self, tag: str) -> int:
        return sum(1 for e in self.events if isinstance(e, NoBid) and e.tag == tag)


# ------------------------------------------------------------------ parser

_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _first_object(text: str):
    """Return the first balanced {...} block in text, or None."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def _candidates(raw: str):
    """Yield strings to try as JSON, most to least strict."""
    s = raw.strip()
    yield s
    m = _FENCE.search(s)
    if m:
        yield m.group(1)
    obj = _first_object(s)
    if obj:
        yield obj


def _to_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.strip().lower() in ("true", "yes"):
            return True
        if v.strip().lower() in ("false", "no"):
            return False
    if isinstance(v, (int, float)):
        return bool(v)
    raise ValueError(f"bid flag not boolean: {v!r}")


def _to_confidence(v):
    """Return (confidence on 0-100, normalized?)."""
    normalized = False
    if isinstance(v, str):
        s = v.strip()
        if s.endswith("%"):
            s, normalized = s[:-1], True
        v = float(s)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"confidence not a number: {v!r}")
    c = float(v)
    if 0.0 < c <= 1.0 and not float(c).is_integer():      # 0.9 -> 90
        c, normalized = c * 100.0, True
    if not 0.0 <= c <= 100.0:
        raise ValueError(f"confidence out of range: {c}")
    return c, normalized


def parse_bid(contractor: str, raw: str):
    """Turn a model reply into a Bid, or a NoBid(parse_fail) if no JSON with
    the required fields can be found. The raw text is kept either way."""
    for text in _candidates(raw or ""):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "bid" not in data:
            continue
        try:
            participate = _to_bool(data["bid"])
            if "confidence" in data:
                conf, normalized = _to_confidence(data["confidence"])
            elif not participate:
                conf, normalized = 0.0, False       # a decline needs no confidence
            else:
                raise ValueError("participating bid without confidence")
        except ValueError:
            continue
        return Bid(contractor, participate, conf, str(data.get("reason", "")),
                   raw=raw, normalized=normalized)
    return NoBid(contractor, "parse_fail", raw=raw or "")
