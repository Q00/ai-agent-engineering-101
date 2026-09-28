"""One buyer/seller negotiation episode for the week-04 experiment."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from acl import system_prompt
from protocol import ReadResult, ReaderCall, read_message


MAX_TURNS = 8

ChatMessage = dict[str, str]
AgentCall = Callable[[str, Sequence[ChatMessage]], str]
LogCall = Callable[[str], None]


@dataclass
class EpisodeResult:
    scenario: int | str
    deal_possible: int
    outcome: str = "open"
    price: int | None = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    note: str = ""
    transcript: list[tuple[str, str]] = field(default_factory=list)


def _validate_scenario(scenario: Mapping[str, object]) -> None:
    required = {"id", "item", "reserve", "budget"}
    missing = required - set(scenario)
    if missing:
        raise ValueError(f"scenario is missing fields: {sorted(missing)}")
    if not isinstance(scenario["item"], str) or not scenario["item"]:
        raise ValueError("scenario item must be a non-empty string")
    for field_name in ("reserve", "budget"):
        value = scenario[field_name]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"scenario {field_name} must be an integer")


def _reader_log(result: ReadResult) -> str:
    parsed = {
        "performative": result.performative,
        "price": result.price,
        "ok": result.ok,
        "reader_calls": result.reader_calls,
    }
    if result.error:
        parsed["error"] = result.error
    if result.reader_output is not None:
        parsed["reader_output"] = result.reader_output
    return "  [reader] " + json.dumps(parsed, ensure_ascii=False)


def _score(result: EpisodeResult, reserve: int, budget: int) -> None:
    if result.outcome == "deal":
        result.violation = int(
            result.price is None
            or result.price < reserve
            or result.price > budget
        )
        result.correct = int(bool(result.deal_possible) and not result.violation)
    else:
        result.violation = 0
        result.correct = int(not result.deal_possible)


def run_episode(
    scenario: Mapping[str, object],
    condition: str,
    agent_call: AgentCall,
    reader_call: ReaderCall | None,
    log: LogCall | None = None,
    max_turns: int = MAX_TURNS,
) -> EpisodeResult:
    """Run one alternating negotiation, beginning with the buyer."""

    _validate_scenario(scenario)
    if max_turns < 1:
        raise ValueError("max_turns must be at least 1")

    emit = log if log is not None else lambda _line: None
    reserve = int(scenario["reserve"])
    budget = int(scenario["budget"])
    item = str(scenario["item"])
    result = EpisodeResult(
        scenario=scenario["id"],
        deal_possible=int(reserve <= budget),
    )

    systems = {
        "buyer": system_prompt("buyer", item, budget, condition),
        "seller": system_prompt("seller", item, reserve, condition),
    }
    histories: dict[str, list[ChatMessage]] = {"buyer": [], "seller": []}
    last_price: dict[str, int | None] = {"buyer": None, "seller": None}
    role, other = "buyer", "seller"
    state_notes: list[str] = []

    for _ in range(max_turns):
        text = agent_call(systems[role], tuple(histories[role])).strip()
        histories[role].append({"role": "assistant", "content": text})
        histories[other].append({"role": "user", "content": text})
        result.transcript.append((role, text))
        result.turns += 1
        emit(f"[{role}] {text}")

        reading = read_message(
            condition,
            text,
            tuple(result.transcript),
            reader_call,
        )
        result.reader_calls += reading.reader_calls
        emit(_reader_log(reading))

        if not reading.ok:
            result.format_errors += 1
        elif reading.performative == "propose":
            last_price[role] = reading.price
        elif reading.performative == "accept-proposal":
            accepted_price = last_price[other]
            if accepted_price is None:
                note = f"turn {result.turns}: accept-proposal without prior proposal"
                state_notes.append(note)
                emit(f"  [state] {note}")
            else:
                result.outcome = "deal"
                result.price = accepted_price
                break
        elif reading.performative == "refuse":
            result.outcome = "no_deal"
            break

        role, other = other, role

    result.note = "; ".join(state_notes)
    _score(result, reserve, budget)
    emit(
        "[result] "
        f"outcome={result.outcome} price={result.price} "
        f"correct={result.correct} violation={result.violation} "
        f"turns={result.turns} format_errors={result.format_errors} "
        f"reader_calls={result.reader_calls}"
    )
    return result
