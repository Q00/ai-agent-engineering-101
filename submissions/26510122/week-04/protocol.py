"""Message formats and deterministic episode verdicts for week 04."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable


PERFORMATIVES = (
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
)

FORMAT_PARAGRAPHS = {
    "free": (
        "Output exactly one short plain-English sentence. Do not include a "
        "performative label, JSON, Markdown, or an explanation outside the message."
    ),
    "tagged": (
        "Output exactly one allowed performative in parentheses, followed by one "
        "short plain-English sentence. Example: (propose) I can offer 100. "
        "Do not output JSON or any additional text."
    ),
    "structured": (
        "Output exactly one JSON object and no additional text. Use "
        '{"performative":"propose","content":{"price":100}} for a proposal. '
        "For every other performative, use an empty content object."
    ),
}

READER_PROMPT = """
You are the fixed protocol reader for a price negotiation. Classify the message as
exactly one of: propose, accept-proposal, reject-proposal, refuse. Extract an
integer price only when the message proposes a price; otherwise use null. A
question or departure that cannot be represented by the four acts is refuse.
Return only one JSON object in this shape:
{"performative":"propose","price":100}
""".strip()


@dataclass(frozen=True)
class ParsedMessage:
    performative: str | None
    price: int | None
    error: str | None = None


@dataclass(frozen=True)
class EpisodeResult:
    outcome: str
    price: int | None
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int


def role_prompt(role: str, scenario: dict, condition: str) -> str:
    """Keep the role text fixed and vary only the final format paragraph."""
    if role not in ("buyer", "seller"):
        raise ValueError(f"unknown role: {role}")
    if condition not in FORMAT_PARAGRAPHS:
        raise ValueError(f"unknown condition: {condition}")

    if role == "buyer":
        private_rule = (
            f"Your private maximum budget is {scenario['budget']}. Never propose "
            "or accept a price above it. Seek the lowest acceptable price. You speak first."
        )
    else:
        private_rule = (
            f"Your private minimum reserve price is {scenario['reserve']}. Never propose "
            "or accept a price below it. Seek the highest acceptable price."
        )

    base = f"""
You are the {role} negotiating the price of {scenario['item']} with one other agent.
{private_rule}
Keep your private limit secret. You may use only these exact four act names:
`propose`, `accept-proposal`, `reject-proposal`, and `refuse`. Propose a price,
accept the other agent's latest proposal, reject it and continue, or refuse and
leave without a deal. An acceptance is valid only when the other agent has made a
price proposal. If the other agent's latest proposal is within your private limit,
accept it instead of making another counterproposal. Respond with one negotiation
message and no commentary.
""".strip()
    return f"{base}\n\n{FORMAT_PARAGRAPHS[condition]}"


def parse_reader_output(raw: str) -> ParsedMessage:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("reader output is not an object")
        performative = data.get("performative")
        if performative not in PERFORMATIVES:
            raise ValueError("reader returned an unknown performative")
        price = data.get("price")
        if performative == "propose":
            if type(price) is not int:
                raise ValueError("propose requires an integer price")
        elif price is not None:
            raise ValueError("non-propose reader output must use null price")
        return ParsedMessage(performative, price)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return ParsedMessage(None, None, str(exc))


def parse_free(raw: str, reader: Callable[[str], str]) -> ParsedMessage:
    return parse_reader_output(reader(raw))


def parse_tagged(raw: str, reader: Callable[[str], str]) -> ParsedMessage:
    match = re.fullmatch(
        r"\s*\((propose|accept-proposal|reject-proposal|refuse)\)\s+(.+?)\s*",
        raw,
        flags=re.DOTALL,
    )
    if not match:
        return ParsedMessage(None, None, "tagged message does not match the required shape")
    performative, sentence = match.groups()
    if performative != "propose":
        return ParsedMessage(performative, None)
    read = parse_reader_output(reader(sentence))
    if read.error:
        return read
    if read.price is None:
        return ParsedMessage(None, None, "reader did not find a proposal price")
    return ParsedMessage(performative, read.price)


def parse_structured(raw: str) -> ParsedMessage:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"performative", "content"}:
            raise ValueError("structured message needs only performative and content")
        performative = data["performative"]
        content = data["content"]
        if performative not in PERFORMATIVES:
            raise ValueError("unknown performative")
        if not isinstance(content, dict):
            raise ValueError("content must be an object")
        if performative == "propose":
            if set(content) != {"price"} or type(content.get("price")) is not int:
                raise ValueError("propose content needs one integer price")
            price = content["price"]
        else:
            if content:
                raise ValueError("non-propose content must be empty")
            price = None
        return ParsedMessage(performative, price)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return ParsedMessage(None, None, str(exc))


def verdict(
    scenario: dict,
    outcome: str,
    price: int | None,
    turns: int,
    format_errors: int,
    reader_calls: int,
) -> EpisodeResult:
    possible = scenario["reserve"] <= scenario["budget"]
    valid_price = (
        price is not None
        and scenario["reserve"] <= price <= scenario["budget"]
    )
    violation = int(outcome == "deal" and not valid_price)
    correct = int(
        (possible and outcome == "deal" and valid_price)
        or (not possible and outcome == "no_deal")
    )
    return EpisodeResult(
        outcome, price, correct, violation, turns, format_errors, reader_calls
    )
