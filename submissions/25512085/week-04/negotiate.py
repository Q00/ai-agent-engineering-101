"""One two-agent price-negotiation episode for Week 04."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from acl import MAX_TURNS, system_prompt
from protocol import ReaderMeter, read


ModelCaller = Callable[[str, str], str]
LogFunction = Callable[[str], None]


@dataclass
class EpisodeResult:
    scenario_id: str
    deal_possible: int
    outcome: str = "open"
    price: int | None = None
    correct: int = 0
    violation: int = 0
    turns: int = 0
    format_errors: int = 0
    reader_calls: int = 0
    transcript: list[tuple[str, str]] = field(default_factory=list)


def _as_int(scenario: Mapping[str, object], field_name: str) -> int:
    value = scenario[field_name]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"scenario {field_name} must be an integer")
    return value


def _agent_input(transcript: list[tuple[str, str]]) -> str:
    if not transcript:
        return "No messages have been exchanged. It is your turn to respond."
    lines = "\n".join(f"{role}: {text}" for role, text in transcript)
    return f"Conversation so far:\n{lines}\n\nIt is your turn to respond."


def _finish(result: EpisodeResult, reserve: int, budget: int) -> None:
    if result.outcome == "deal":
        assert result.price is not None
        result.violation = int(result.price < reserve or result.price > budget)
        result.correct = int(result.deal_possible == 1 and result.violation == 0)
    elif result.outcome == "no_deal":
        result.correct = int(result.deal_possible == 0)
    # An open negotiation never established the required outcome, even if no deal
    # would ultimately have been possible.


def run_episode(
    scenario: Mapping[str, object],
    condition: str,
    caller: ModelCaller,
    log: LogFunction,
) -> EpisodeResult:
    """Run one Buyer-first episode and return its measured outcome."""
    item = str(scenario["item"])
    reserve = _as_int(scenario, "reserve")
    budget = _as_int(scenario, "budget")
    result = EpisodeResult(
        scenario_id=str(scenario["id"]),
        deal_possible=int(reserve <= budget),
    )
    prompts = {
        "buyer": system_prompt("buyer", item, budget, condition),
        "seller": system_prompt("seller", item, reserve, condition),
    }
    last_price: dict[str, int | None] = {"buyer": None, "seller": None}
    reader_meter = ReaderMeter()
    role, other = "buyer", "seller"

    for _ in range(MAX_TURNS):
        raw = caller(prompts[role], _agent_input(result.transcript))
        result.turns += 1
        log(f"[{role}] {raw}")

        performative, price, ok = read(
            condition,
            raw,
            result.transcript,
            caller,
            reader_meter,
        )
        result.transcript.append((role, raw))
        log(
            f"[protocol] performative={performative} price={price} "
            f"parsed={ok} reader_calls={reader_meter.calls}"
        )

        if not ok:
            result.format_errors += 1
        elif performative == "propose":
            last_price[role] = price
        elif performative == "accept-proposal":
            accepted_price = last_price[other]
            if accepted_price is None:
                log("[protocol] acceptance ignored: no recorded opposing proposal")
            else:
                result.outcome = "deal"
                result.price = accepted_price
                break
        elif performative == "refuse":
            result.outcome = "no_deal"
            break

        role, other = other, role

    result.reader_calls = reader_meter.calls
    _finish(result, reserve, budget)
    log(
        f"[episode] scenario={result.scenario_id} outcome={result.outcome} "
        f"price={result.price} correct={result.correct} violation={result.violation} "
        f"turns={result.turns} format_errors={result.format_errors} "
        f"reader_calls={result.reader_calls}"
    )
    return result
