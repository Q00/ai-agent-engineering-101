"""Condition-specific message contracts for the Week 04 negotiation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable


CONDITIONS = ("free", "tagged", "structured")
PERFORMATIVES = (
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
)

FORMAT_PARAGRAPHS = {
    "free": (
        "Write one short, natural English message. Do not add a performative "
        "tag, JSON, metadata, or commentary. Your wording must clearly express "
        "exactly one allowed act. Include one integer price when you propose."
    ),
    "tagged": (
        "Write exactly one performative tag in parentheses, then one short "
        "natural English message. The tag must be one of (propose), "
        "(accept-proposal), (reject-proposal), or (refuse). Include one integer "
        "price in the English text when you propose."
    ),
    "structured": (
        "Write only one JSON object with exactly this shape: "
        '{"performative":"propose|accept-proposal|reject-proposal|refuse",'
        '"content":{"price":integer_or_null}}. Use an integer only for '
        "propose and null for every other act. Do not use a Markdown fence."
    ),
}

FREE_READER_PROMPT = (
    "You are the protocol reader for a price negotiation. Classify the message "
    "as exactly one of propose, accept-proposal, reject-proposal, or refuse. "
    "Extract the integer price only when the act is propose; otherwise use null. "
    "Return only one JSON object with exactly the keys performative and price."
)

PRICE_READER_PROMPT = (
    "Extract the single integer price proposed in this negotiation message. "
    "Return only one JSON object with exactly the key price. Use null if no "
    "unambiguous integer price is present."
)

ReaderCall = Callable[[str, str], str]


@dataclass(frozen=True)
class ParsedMessage:
    performative: str | None
    price: int | None
    valid: bool
    error: str = ""
    reader_calls: int = 0
    reader_raw: str = ""


def _json_object(raw: str) -> dict:
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("message is not a JSON object")
    return value


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(performative: object, price: object) -> tuple[str, int | None]:
    if performative not in PERFORMATIVES:
        raise ValueError("unknown performative")
    if performative == "propose":
        if not _is_integer(price):
            raise ValueError("propose requires one integer price")
        return str(performative), int(price)
    if price is not None:
        raise ValueError("only propose may contain a price")
    return str(performative), None


def parse_free(raw: str, reader_call: ReaderCall) -> ParsedMessage:
    """Use one LLM reader call to classify every free-form message."""
    reader_raw = ""
    try:
        reader_raw = reader_call(FREE_READER_PROMPT, raw)
        data = _json_object(reader_raw)
        if set(data) != {"performative", "price"}:
            raise ValueError("reader fields must be exactly performative and price")
        act, price = _validate(data["performative"], data["price"])
        return ParsedMessage(act, price, True, reader_calls=1,
                             reader_raw=reader_raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return ParsedMessage(None, None, False, str(exc), 1, reader_raw)


TAG_RE = re.compile(
    r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s*(.*?)\s*$",
    re.DOTALL,
)


def parse_tagged(raw: str, reader_call: ReaderCall) -> ParsedMessage:
    """Read the tag with regex and use an LLM only for a proposal price."""
    match = TAG_RE.fullmatch(raw)
    if not match:
        return ParsedMessage(None, None, False, "missing or invalid performative tag")
    if len(TAG_RE.findall(raw)) != 1:
        return ParsedMessage(None, None, False, "expected exactly one tag")

    performative, content = match.groups()
    if performative != "propose":
        return ParsedMessage(performative, None, True)

    reader_raw = ""
    try:
        reader_raw = reader_call(PRICE_READER_PROMPT, content)
        data = _json_object(reader_raw)
        if set(data) != {"price"}:
            raise ValueError("reader field must be exactly price")
        act, price = _validate(performative, data["price"])
        return ParsedMessage(act, price, True, reader_calls=1,
                             reader_raw=reader_raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return ParsedMessage(None, None, False, str(exc), 1, reader_raw)


def parse_structured(raw: str) -> ParsedMessage:
    """Parse the structured condition without an LLM reader."""
    try:
        data = _json_object(raw)
        if set(data) != {"performative", "content"}:
            raise ValueError("fields must be exactly performative and content")
        content = data["content"]
        if not isinstance(content, dict) or set(content) != {"price"}:
            raise ValueError("content field must contain exactly price")
        act, price = _validate(data["performative"], content["price"])
        return ParsedMessage(act, price, True)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return ParsedMessage(None, None, False, str(exc))


def parse_message(condition: str, raw: str,
                  reader_call: ReaderCall) -> ParsedMessage:
    if condition == "free":
        return parse_free(raw, reader_call)
    if condition == "tagged":
        return parse_tagged(raw, reader_call)
    if condition == "structured":
        return parse_structured(raw)
    raise ValueError(f"unknown condition: {condition}")
