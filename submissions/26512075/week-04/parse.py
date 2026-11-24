from __future__ import annotations
import json
import re

from typing import Any, Optional

from prompts import READER_PROMPT
from typeset import PERFORMATIVES, Condition, ParsedAct, Performative

TAG_RE = re.compile(
    r"^\s*\[(propose|accept-proposal|reject-proposal|refuse)\]\s*(.*)$",
    re.IGNORECASE | re.DOTALL,
)

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

def _as_performative(value: Any) -> Optional[Performative]:
    if not isinstance(value, str):
        return None
    
    v = value.strip().lower()

    return v if v in PERFORMATIVES else None

def _as_price(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value.replace(",", ""))
        if m:
            return int(m.group(0))
    return None

def extract_json_obj(text: str) -> Optional[dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = JSON_RE.search(text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

def parse_reader_payload(text: str) -> tuple[Optional[Performative], Optional[int]]:
    obj = extract_json_obj(text) or {}
    return _as_performative(obj.get("performative")), _as_price(obj.get("price"))


def parse_structured(raw: str) -> ParsedAct:
    
    try:
        obj = json.loads(raw)
    
    except json.JSONDecodeError:
        return ParsedAct(None, None, True, 0)
    
    if not isinstance(obj, dict):
        return ParsedAct(None, None, True, 0)
    

    act = _as_performative(obj.get("performative"))

    if act is None:
        return ParsedAct(None, None, True, 0)
    if act != "propose":
        return ParsedAct(act, None, False, 0)
    
    content = obj.get("content")

    if not isinstance(content, dict):
        return ParsedAct(act, None, True, 0)
    
    price = content.get("price")

    if type(price) is not int:
        return ParsedAct(act, None, True, 0)

    return ParsedAct(act, price, False, 0)


def parse_tagged_tag(raw: str) -> tuple[Optional[Performative], str]:
    m = TAG_RE.match(raw.strip())
    if not m:
        return None, raw
    return _as_performative(m.group(1)), m.group(2).strip()

class ProtocolLayer:
    """
    Reads surface foam.
    Transport-agnostic; (Kafka can reuser parse_* later.)
    """

    def __init__(self, reader_fn):
        self.reader_fn = reader_fn
    
    def parse(self, condition: Condition, raw: str, conversation: list[str]) -> ParsedAct:
        if condition == "structured":
            return parse_structured(raw)
        if condition == "tagged":
            return self._parse_tagged(raw)
        if condition == "free":
            return self._parse_free(raw, conversation)

        
        raise ValueError(f"unknown condition: {condition}")

    def _parse_tagged(self, raw: str) -> ParsedAct:
        performative, rest = parse_tagged_tag(raw)

        if performative is None:
            return ParsedAct(None, None, True, 0)
        if performative != "propose":
            return ParsedAct(performative, None, False, 0)
        price, raw_reader = self._call_reader(
            "Extract the speaker's offered price from this tagged propose.\n\n" + rest
        )
        if price is None:
            return ParsedAct(performative, None, True, 1, raw_reader)
        
        return ParsedAct(performative, price, False, 1, raw_reader)
    
    def _parse_free(self, raw: str, conversation: list[str]) -> ParsedAct:
        previous = "\n".join(conversation) if conversation else "(none)"
        body = (
            f"Previous negotiation messages:\n{previous}\n\n"
            f"LAST message to label:\n{raw}\n\n"
            "Label the LAST message only."
        )
        performative, price, raw_reader = self._call_reader_full(body)

        if performative is None:
            return ParsedAct(None, price, True, 1, raw_reader)
        if performative == "propose" and price is None:
            return ParsedAct(performative, None, True, 1, raw_reader)

        return ParsedAct(performative, price, False, 1, raw_reader)

    def _call_reader(self, user_text: str) -> tuple[Optional[int], str]:
        raw = self.reader_fn(READER_PROMPT, user_text)
        _perf, price = parse_reader_payload(raw)
        return price, raw

    def _call_reader_full(self, user_text: str) -> tuple[Optional[Performative], Optional[int], str]:
        raw = self.reader_fn(READER_PROMPT, user_text)
        performative, price = parse_reader_payload(raw)

        return performative, price, raw