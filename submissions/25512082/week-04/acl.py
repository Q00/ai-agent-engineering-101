"""Prompts and protocol readers for free, tagged, and structured messages."""

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

BUYER_ROLE = (
    "You are the buyer negotiating to buy {item}. Your private maximum budget is "
    "{limit}. Never knowingly agree to pay more than this budget."
)
SELLER_ROLE = (
    "You are the seller negotiating to sell {item}. Your private minimum reserve "
    "price is {limit}. Never knowingly agree to sell below this reserve."
)
COMMON_RULES = (
    " Negotiate directly with the other party. The buyer sends the first message. "
    "Use only these actions: propose a whole-number price, accept-proposal to accept "
    "the other party's latest proposed price, reject-proposal to decline and continue, "
    "or refuse to leave the negotiation. Do not reveal your private limit."
)
FORMAT_INSTRUCTIONS = {
    "free": " Reply in one plain English message.",
    "tagged": (
        " Begin the message with exactly one of (propose), (accept-proposal), "
        "(reject-proposal), or (refuse), then write one plain English sentence."
    ),
    "structured": (
        ' Reply with exactly one JSON object and nothing else: {"performative": '
        '"propose" | "accept-proposal" | "reject-proposal" | "refuse", '
        '"content": {"price": <whole number or null>}}.'
    ),
}

READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. A propose offers a new price; accept-proposal agrees "
    "to the other party's previous proposed price; reject-proposal declines but keeps "
    "negotiating; refuse leaves the negotiation. For propose, price is the new price "
    "offered by the last speaker, not a quoted earlier price or a private limit. Reply "
    "with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
    '"refuse", "price": <whole number or null>}.'
)

Reader = Callable[[list[dict[str, str]]], str]


@dataclass(frozen=True)
class ParsedMessage:
    performative: str | None
    price: int | None
    ok: bool
    reader_calls: int = 0
    error: str = ""
    reader_raw: str | None = None


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if role == "buyer":
        base = BUYER_ROLE.format(item=item, limit=limit)
    elif role == "seller":
        base = SELLER_ROLE.format(item=item, limit=limit)
    else:
        raise ValueError(f"unknown role: {role}")
    return base + COMMON_RULES + FORMAT_INSTRUCTIONS[condition]


def _whole_number(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _reader_json(raw: str) -> tuple[str | None, int | None, str]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, None, f"reader invalid JSON: {exc}"
    if not isinstance(value, dict) or set(value) != {"performative", "price"}:
        return None, None, "reader object must contain exactly performative and price"
    performative = value["performative"]
    if performative not in PERFORMATIVES:
        return None, None, "reader returned an unsupported performative"
    price = _whole_number(value["price"])
    if performative == "propose" and price is None:
        return None, None, "reader propose requires a non-negative integer price"
    return performative, price, ""


def read_free(transcript: list[dict[str, str]], reader: Reader) -> ParsedMessage:
    raw = reader(transcript)
    performative, price, error = _reader_json(raw)
    return ParsedMessage(
        performative,
        price,
        not error,
        reader_calls=1,
        error=error,
        reader_raw=raw,
    )


TAG_PATTERN = re.compile(
    r"^\((propose|accept-proposal|reject-proposal|refuse)\)(?=\s|$)"
)


def read_tagged(
    raw_message: str,
    transcript: list[dict[str, str]],
    reader: Reader,
) -> ParsedMessage:
    match = TAG_PATTERN.match(raw_message)
    if not match:
        return ParsedMessage(None, None, False, error="missing or invalid leading tag")
    performative = match.group(1)
    if performative != "propose":
        return ParsedMessage(performative, None, True)

    raw = reader(transcript)
    _reader_performative, price, error = _reader_json(raw)
    if error or price is None:
        return ParsedMessage(
            None,
            None,
            False,
            reader_calls=1,
            error=error or "reader did not return a proposal price",
            reader_raw=raw,
        )
    return ParsedMessage(
        performative,
        price,
        True,
        reader_calls=1,
        reader_raw=raw,
    )


def read_structured(raw_message: str) -> ParsedMessage:
    try:
        value = json.loads(raw_message)
    except (json.JSONDecodeError, TypeError) as exc:
        return ParsedMessage(None, None, False, error=f"invalid JSON: {exc}")
    if not isinstance(value, dict) or set(value) != {"performative", "content"}:
        return ParsedMessage(
            None, None, False, error="object must contain exactly performative and content"
        )
    performative = value["performative"]
    if performative not in PERFORMATIVES:
        return ParsedMessage(None, None, False, error="unsupported performative")
    content = value["content"]
    if not isinstance(content, dict) or set(content) != {"price"}:
        return ParsedMessage(None, None, False, error="content must contain exactly price")
    price = _whole_number(content["price"])
    if performative == "propose" and price is None:
        return ParsedMessage(
            None, None, False, error="propose requires a non-negative integer price"
        )
    return ParsedMessage(performative, price, True)


def read_message(
    condition: str,
    raw_message: str,
    transcript: list[dict[str, str]],
    reader: Reader | None,
) -> ParsedMessage:
    if condition == "structured":
        return read_structured(raw_message)
    if reader is None:
        raise ValueError(f"{condition} requires a reader")
    if condition == "free":
        return read_free(transcript, reader)
    if condition == "tagged":
        return read_tagged(raw_message, transcript, reader)
    raise ValueError(f"unknown condition: {condition}")
