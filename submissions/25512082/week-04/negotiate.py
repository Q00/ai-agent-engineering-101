"""Deterministic negotiation engine; model behavior is injected by callables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from acl import ParsedMessage, Reader, read_message, system_prompt


MAX_MESSAGES = 8
Agent = Callable[[str, str, list[dict[str, str]]], str]
EventLogger = Callable[[str], None]


@dataclass(frozen=True)
class EpisodeResult:
    outcome: str
    price: int | None
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int
    note: str
    transcript: tuple[dict[str, str], ...]


def calculate_metrics(
    reserve: int,
    budget: int,
    outcome: str,
    price: int | None,
) -> tuple[int, int]:
    deal_possible = reserve <= budget
    violation = int(
        outcome == "deal"
        and price is not None
        and (price < reserve or price > budget)
    )
    if deal_possible:
        correct = int(outcome == "deal" and price is not None and not violation)
    else:
        correct = int(outcome == "no_deal")
    return correct, violation


def run_episode(
    scenario: dict,
    condition: str,
    agent: Agent,
    reader: Reader | None,
    log: EventLogger | None = None,
    max_messages: int = MAX_MESSAGES,
) -> EpisodeResult:
    emit = log or (lambda _message: None)
    systems = {
        "buyer": system_prompt("buyer", scenario["item"], scenario["budget"], condition),
        "seller": system_prompt(
            "seller", scenario["item"], scenario["reserve"], condition
        ),
    }
    histories: dict[str, list[dict[str, str]]] = {"buyer": [], "seller": []}
    transcript: list[dict[str, str]] = []
    last_proposal: dict[str, int | None] = {"buyer": None, "seller": None}
    role = "buyer"
    outcome = "open"
    deal_price: int | None = None
    format_errors = 0
    reader_calls = 0
    notes: list[str] = []

    for turn in range(1, max_messages + 1):
        other = "seller" if role == "buyer" else "buyer"
        raw = agent(role, systems[role], list(histories[role]))

        # Delivery precedes protocol parsing, including for malformed messages.
        histories[role].append({"role": "assistant", "content": raw})
        histories[other].append({"role": "user", "content": raw})
        transcript.append({"speaker": role, "content": raw})
        emit(f"[{role}] {raw}")

        parsed: ParsedMessage = read_message(condition, raw, transcript, reader)
        reader_calls += parsed.reader_calls
        emit(
            "[protocol] "
            f"ok={int(parsed.ok)} performative={parsed.performative} "
            f"price={parsed.price} reader_calls={parsed.reader_calls} "
            f"error={parsed.error or '-'} reader_raw={parsed.reader_raw or '-'}"
        )
        if not parsed.ok:
            format_errors += 1
            role = other
            continue

        if parsed.performative == "propose":
            last_proposal[role] = parsed.price
        elif parsed.performative == "accept-proposal":
            if last_proposal[other] is not None:
                outcome = "deal"
                deal_price = last_proposal[other]
                break
            notes.append(f"turn {turn}: accept-proposal without opponent proposal")
        elif parsed.performative == "refuse":
            outcome = "no_deal"
            break
        role = other

    correct, violation = calculate_metrics(
        scenario["reserve"], scenario["budget"], outcome, deal_price
    )
    result = EpisodeResult(
        outcome=outcome,
        price=deal_price,
        correct=correct,
        violation=violation,
        turns=len(transcript),
        format_errors=format_errors,
        reader_calls=reader_calls,
        note="; ".join(notes),
        transcript=tuple(transcript),
    )
    emit(
        "[result] "
        f"outcome={result.outcome} price={result.price} correct={result.correct} "
        f"violation={result.violation} turns={result.turns} "
        f"format_errors={result.format_errors} reader_calls={result.reader_calls} "
        f"note={result.note or '-'}"
    )
    return result
