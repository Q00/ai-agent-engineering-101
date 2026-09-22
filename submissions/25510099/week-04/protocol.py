"""The protocol layer: how the harness reads a message in each condition.

A Reading is what the harness learns from one message: the performative, the
price if any, how many model calls it spent, and why it failed if it did. The
agents never see a Reading; they only see each other's raw text. The Reading
drives the bookkeeping and the end of the episode.

  FreeReader        one model call per message; the reader labels act and price
  TaggedReader      regex for the tag; one model call for the price of a propose
  StructuredReader  json.loads; no model call
"""
import json
import re
from dataclasses import dataclass

from prompts import (ACTS, READER_FREE, READER_FREE_INPUT, READER_PRICE,
                     READER_PRICE_INPUT)


@dataclass
class Reading:
    performative: str | None      # one of ACTS, or None when unreadable
    price: int | None
    reader_calls: int
    error: str | None = None      # set when the protocol layer could not parse the message
    warning: str | None = None    # parsed fine, but force and content disagree (logged, not counted)

    def __str__(self):
        s = f"performative={self.performative} price={self.price} reader_calls={self.reader_calls}"
        s += f" FORMAT-ERROR: {self.error}" if self.error else ""
        return s + (f" WARNING: {self.warning}" if self.warning else "")


def to_int(x):
    """A price from model output: 180, 180.0, "180", "$180", "1,200"."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return int(x) if float(x).is_integer() else None
    s = re.sub(r"[^\d.\-]", "", str(x))
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else None


def loads_object(text: str):
    """Strictly one JSON object. Raises ValueError otherwise."""
    obj = json.loads(text.strip())
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    return obj


class LLMReader:
    """Shared plumbing: one fresh Chat per read, temperature and model as the
    agents', counted on its own Meter so reader calls stay separate."""

    def __init__(self, chat_factory, meter):
        self.chat_factory = chat_factory
        self.meter = meter

    def ask(self, system: str, user: str) -> str:
        chat = self.chat_factory(system, self.meter)
        chat.add_user(user)
        return chat.send()


class FreeReader(LLMReader):
    name = "free"

    def read(self, text: str, speaker: str, previous: str | None) -> Reading:
        other = "seller" if speaker == "buyer" else "buyer"
        user = READER_FREE_INPUT.format(
            other=other, speaker=speaker, text=json.dumps(text),
            previous=json.dumps(previous) if previous else "(none; this is the opening message)")
        raw = self.ask(READER_FREE, user)
        try:
            obj = loads_object(raw)
        except ValueError as e:
            return Reading(None, None, 1, f"reader output is not JSON ({e}): {raw!r}")
        perf = str(obj.get("performative", "")).strip().lower()
        price = to_int(obj.get("price"))
        if perf not in ACTS:
            return Reading(None, price, 1, f"reader gave unknown performative {perf!r}")
        if perf == "propose" and price is None:
            return Reading(perf, None, 1, "reader labelled propose without a price")
        return Reading(perf, price, 1)


TAG_RE = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)", re.IGNORECASE)


class TaggedReader(LLMReader):
    name = "tagged"

    def read(self, text: str, speaker: str, previous: str | None) -> Reading:
        m = TAG_RE.match(text)
        if not m:
            return Reading(None, None, 0, "no performative tag at the start of the message")
        perf = m.group(1).lower()
        body = text[m.end():].strip()
        if perf != "propose":
            # The tag is authoritative, as in FIPA-ACL: a price in the body of a
            # reject or refuse is not a proposal. It is flagged so the report can
            # count how often force and content disagree.
            n = re.search(r"\d+", body)
            warn = f"tag is {perf} but the body names {n.group(0)}" if n else None
            return Reading(perf, None, 0, warning=warn)
        raw = self.ask(READER_PRICE, READER_PRICE_INPUT.format(text=json.dumps(body)))
        try:
            price = to_int(loads_object(raw).get("price"))
        except ValueError as e:
            return Reading(perf, None, 1, f"price reader output is not JSON ({e}): {raw!r}")
        if price is None:
            return Reading(perf, None, 1, "propose tag but the price reader found no price")
        return Reading(perf, price, 1)


class StructuredReader:
    name = "structured"

    def __init__(self, chat_factory=None, meter=None):
        pass

    def read(self, text: str, speaker: str, previous: str | None) -> Reading:
        try:
            obj = loads_object(text)
        except ValueError as e:
            return Reading(None, None, 0, f"message is not one JSON object ({e})")
        perf = str(obj.get("performative", "")).strip().lower()
        if perf not in ACTS:
            return Reading(None, None, 0, f"unknown performative {perf!r}")
        content = obj.get("content")
        if not isinstance(content, dict):
            return Reading(perf, None, 0, "content is not an object")
        price = to_int(content.get("price"))
        if perf == "propose" and price is None:
            return Reading(perf, None, 0, "propose without an integer price")
        return Reading(perf, price, 0)


READERS = {"free": FreeReader, "tagged": TaggedReader, "structured": StructuredReader}
