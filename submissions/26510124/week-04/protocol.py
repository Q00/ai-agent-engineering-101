"""Read speech acts without repairing or reinterpreting protocol failures.

The free reader must return a complete JSON object. The structured condition
accepts a JSON object at the start (after whitespace) and ignores its suffix.
Both reject duplicate keys, non-finite numbers, booleans, and fractional or
negative prices. Required fields are checked; additional fields are ignored.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Callable

from prompts import CONDITIONS, PERFORMATIVES, READER_SYSTEM

if TYPE_CHECKING:
    from model_client import ModelReply


Reader = Callable[[list[dict[str, str]]], "ModelReply"]
Logger = Callable[[dict], None]
TAG = re.compile(r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s+")


@dataclass(frozen=True)
class ParseResult:
    ok: bool
    performative: str | None = None
    price: int | None = None
    error: str = ""
    trailing: str = ""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite_number")


def _decode(raw: str, *, leading: bool = False) -> tuple[object, str]:
    decoder = json.JSONDecoder(
        object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )
    if leading:
        message = raw.lstrip()
        parsed, end = decoder.raw_decode(message)
        return parsed, message[end:]
    return decoder.decode(raw), ""


def _is_price(value: object) -> bool:
    # bool subclasses int in Python, but true is not a negotiated price.
    return type(value) is int and value >= 0


def _schema_result(payload: object, *, structured: bool, trailing: str = "") -> ParseResult:
    if not isinstance(payload, dict):
        return ParseResult(False, error="object_required", trailing=trailing)
    performative = payload.get("performative")
    if not isinstance(performative, str) or performative not in PERFORMATIVES:
        return ParseResult(False, error="invalid_performative", trailing=trailing)
    content = payload.get("content") if structured else payload
    if not isinstance(content, dict):
        return ParseResult(False, performative, error="content_object_required", trailing=trailing)
    if "price" not in content:
        return ParseResult(False, performative, error="price_field_required", trailing=trailing)
    price = content["price"]
    if performative == "propose":
        if not _is_price(price):
            return ParseResult(False, performative, error="propose_price_required", trailing=trailing)
        return ParseResult(True, performative, price, trailing=trailing)
    if price is not None and not _is_price(price):
        return ParseResult(False, performative, error="invalid_price_type", trailing=trailing)
    # An acceptance's price never overrides the OTHER actor's last proposal.
    return ParseResult(True, performative, trailing=trailing)


def _read(transcript: list[dict[str, str]], reader: Reader, log: Logger) -> str:
    messages = [
        {"role": "system", "content": READER_SYSTEM},
        {"role": "user", "content": json.dumps(transcript, ensure_ascii=False)},
    ]
    reply = reader(messages)
    log({"event": "reader_result", "raw": reply.text})
    return reply.text


def read_message(
    condition: str,
    raw: str,
    transcript: list[dict[str, str]],
    reader: Reader,
    log: Logger,
) -> ParseResult:
    """Parse one delivered message, calling the reader only when required.

    Reader transport errors propagate so the harness preserves a crashed
    episode instead of miscounting an API failure as a format error.
    """
    if condition not in CONDITIONS:
        raise ValueError("unknown condition")
    if condition == "structured":
        try:
            payload, trailing = _decode(raw, leading=True)
        except (json.JSONDecodeError, ValueError, TypeError):
            result = ParseResult(False, error="invalid_json")
        else:
            result = _schema_result(payload, structured=True, trailing=trailing)
    elif condition == "free":
        reader_raw = _read(transcript, reader, log)
        try:
            payload, _ = _decode(reader_raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            result = ParseResult(False, error="invalid_reader_json")
        else:
            result = _schema_result(payload, structured=False)
    else:
        match = TAG.match(raw)
        if not match:
            result = ParseResult(False, error="missing_leading_tag")
        else:
            performative = match.group(1)
            if performative != "propose":
                result = ParseResult(True, performative)
            else:
                reader_raw = _read(transcript, reader, log)
                try:
                    payload, _ = _decode(reader_raw)
                except (json.JSONDecodeError, ValueError, TypeError):
                    result = ParseResult(False, performative, error="invalid_reader_json")
                else:
                    if not isinstance(payload, dict) or not _is_price(payload.get("price")):
                        result = ParseResult(False, performative, error="propose_price_required")
                    else:
                        # The explicit tag owns the force; the reader supplies only price.
                        result = ParseResult(True, performative, payload["price"])
    log({"event": "parse_result", **asdict(result)})
    return result
