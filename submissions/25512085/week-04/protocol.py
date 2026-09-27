"""Strict readers for the three Week 04 negotiation message formats."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from acl import PERFORMATIVES, READER_SYSTEM


TAG_PATTERN = re.compile(
    r"^\((propose|accept-proposal|reject-proposal|refuse)\)(?:\s+|$)"
)
ModelCaller = Callable[[str, str], str]


@dataclass
class ReaderMeter:
    """Per-episode count of calls used to interpret received messages."""

    calls: int = 0


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _transcript_text(transcript: Sequence[tuple[str, str]], text: str) -> str:
    history = "\n".join(f"{role}: {message}" for role, message in transcript)
    return f"Conversation so far:\n{history}\n\nLAST MESSAGE:\n{text}"


def _read_with_model(
    text: str,
    transcript: Sequence[tuple[str, str]],
    caller: ModelCaller,
    reader_meter: ReaderMeter,
) -> tuple[str | None, int | None, bool]:
    """Use the common reader prompt and strictly parse its JSON response."""
    reader_meter.calls += 1
    try:
        raw = caller(READER_SYSTEM, _transcript_text(transcript, text))
        payload = json.loads(raw)
        performative = payload["performative"]
        price = payload["price"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None, None, False

    if performative not in PERFORMATIVES:
        return None, None, False
    if performative == "propose":
        if not _is_whole_number(price):
            return None, None, False
    elif price is not None:
        return None, None, False
    return performative, price, True


def read_free(
    text: str,
    transcript: Sequence[tuple[str, str]],
    caller: ModelCaller,
    reader_meter: ReaderMeter,
) -> tuple[str | None, int | None, bool]:
    """Read a plain-English message with an LLM reader on every turn."""
    return _read_with_model(text, transcript, caller, reader_meter)


def read_tagged(
    text: str,
    transcript: Sequence[tuple[str, str]],
    caller: ModelCaller,
    reader_meter: ReaderMeter,
) -> tuple[str | None, int | None, bool]:
    """Read the tag by regex; use the reader only to extract a proposal price."""
    match = TAG_PATTERN.match(text)
    if not match:
        return None, None, False

    performative = match.group(1)
    if performative != "propose":
        return performative, None, True

    reader_meter.calls += 1
    try:
        raw = caller(
            'Extract the whole-number price stated in the LAST message. The propose '
            'tag already determines the act; do not classify the act again. '
            'Reply with exactly one JSON object: {"price": <whole number or null>}. '
            'Use null if no single proposal price can be identified.',
            _transcript_text(transcript, text),
        )
        price = json.loads(raw)["price"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None, None, False
    if not _is_whole_number(price):
        return None, None, False
    return "propose", price, True


def read_structured(text: str) -> tuple[str | None, int | None, bool]:
    """Read one exact JSON message without any model call or repair."""
    try:
        payload = json.loads(text)
        performative = payload["performative"]
        content = payload["content"]
        price = content["price"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None, None, False

    if not isinstance(payload, dict) or set(payload) != {"performative", "content"}:
        return None, None, False
    if performative not in PERFORMATIVES or not isinstance(content, dict):
        return None, None, False
    if performative == "propose":
        if not _is_whole_number(price):
            return None, None, False
    else:
        # The act determines termination. A quoted acceptance price must not
        # replace the opposing party's last recorded proposal.
        price = None
    return performative, price, True


def read(
    condition: str,
    text: str,
    transcript: Sequence[tuple[str, str]],
    caller: ModelCaller,
    reader_meter: ReaderMeter,
) -> tuple[str | None, int | None, bool]:
    """Return (performative, proposed_price, parsed_ok) for one raw message."""
    if condition == "free":
        return read_free(text, transcript, caller, reader_meter)
    if condition == "tagged":
        return read_tagged(text, transcript, caller, reader_meter)
    if condition == "structured":
        return read_structured(text)
    raise ValueError(f"unknown condition: {condition}")
