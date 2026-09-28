"""Protocol readers for free, tagged, and structured negotiation messages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Sequence

from acl import READER_SYSTEM


PERFORMATIVES = {
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
}

TAG_RE = re.compile(
    r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s+(.+)$",
    re.DOTALL,
)
ANY_ALLOWED_TAG_RE = re.compile(
    r"\((?:propose|accept-proposal|reject-proposal|refuse)\)"
)

TAGGED_PRICE_READER_SYSTEM = (
    "Read one tagged price-negotiation proposal. Extract the whole-number "
    "price that the speaker is offering, not a rejected or previously "
    "mentioned price. Reply with exactly one JSON object and nothing else: "
    '{"price":<whole number>}.'
)

ReaderCall = Callable[[str, str], str]
Transcript = Sequence[tuple[str, str]]


@dataclass(frozen=True)
class ReadResult:
    """The protocol layer's interpretation of one message."""

    performative: str | None
    price: int | None
    ok: bool
    reader_calls: int
    reader_output: str | None = None
    error: str = ""


def _is_int(value: object) -> bool:
    """Accept JSON integers but reject booleans, which are int subclasses."""

    return isinstance(value, int) and not isinstance(value, bool)


def _invalid(error: str, calls: int = 0, raw: str | None = None) -> ReadResult:
    return ReadResult(None, None, False, calls, raw, error)


def _decode_json_object(raw: str) -> tuple[dict[str, object] | None, str]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(value, dict):
        return None, "message is not a JSON object"
    return value, ""


def read_structured(text: str) -> ReadResult:
    """Read the structured condition without making a model call."""

    data, error = _decode_json_object(text)
    if data is None:
        return _invalid(error)
    if set(data) != {"performative", "content"}:
        return _invalid("object must contain only performative and content")

    performative = data["performative"]
    content = data["content"]
    if performative not in PERFORMATIVES:
        return _invalid("unknown performative")
    if not isinstance(content, dict) or set(content) != {"price"}:
        return _invalid("content must contain only price")

    price = content["price"]
    if performative == "propose":
        if not _is_int(price):
            return _invalid("propose requires a whole-number price")
    elif price is not None:
        return _invalid("non-propose performatives require a null price")

    return ReadResult(performative, price, True, 0)


def read_tagged(text: str, reader: ReaderCall | None) -> ReadResult:
    """Read a leading tag, calling a model only to extract propose's price."""

    match = TAG_RE.fullmatch(text)
    if match is None:
        return _invalid("message must start with one allowed tag and a sentence")
    if len(ANY_ALLOWED_TAG_RE.findall(text)) != 1:
        return _invalid("message must contain exactly one performative tag")

    performative, sentence = match.groups()
    if performative != "propose":
        return ReadResult(performative, None, True, 0)
    if reader is None:
        raise ValueError("tagged propose requires a reader callback")

    raw = reader(TAGGED_PRICE_READER_SYSTEM, sentence)
    data, error = _decode_json_object(raw)
    if data is None:
        return _invalid(error, calls=1, raw=raw)
    if set(data) != {"price"} or not _is_int(data["price"]):
        return _invalid(
            "price reader must return one whole-number price",
            calls=1,
            raw=raw,
        )
    return ReadResult(performative, data["price"], True, 1, raw)


def format_transcript(transcript: Transcript) -> str:
    """Render role-labelled messages for the free-condition reader."""

    return "\n".join(f"[{role}] {message}" for role, message in transcript)


def read_free(transcript: Transcript, reader: ReaderCall | None) -> ReadResult:
    """Ask the reader model to label the final message in a transcript."""

    if not transcript:
        return _invalid("transcript is empty")
    if reader is None:
        raise ValueError("free condition requires a reader callback")

    raw = reader(READER_SYSTEM, format_transcript(transcript))
    data, error = _decode_json_object(raw)
    if data is None:
        return _invalid(error, calls=1, raw=raw)
    if set(data) != {"performative", "price"}:
        return _invalid(
            "reader object must contain only performative and price",
            calls=1,
            raw=raw,
        )

    performative = data["performative"]
    price = data["price"]
    if performative not in PERFORMATIVES:
        return _invalid("reader returned an unknown performative", 1, raw)
    if performative == "propose":
        if not _is_int(price):
            return _invalid("reader propose requires a whole-number price", 1, raw)
    elif price is not None:
        return _invalid("reader non-propose requires a null price", 1, raw)

    return ReadResult(performative, price, True, 1, raw)


def read_message(
    condition: str,
    text: str,
    transcript: Transcript,
    reader: ReaderCall | None = None,
) -> ReadResult:
    """Dispatch one message to the reader required by its condition."""

    if condition == "free":
        return read_free(transcript, reader)
    if condition == "tagged":
        return read_tagged(text, reader)
    if condition == "structured":
        return read_structured(text)
    raise ValueError(f"unknown condition: {condition}")
