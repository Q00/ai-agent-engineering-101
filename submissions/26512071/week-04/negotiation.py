"""Run the Week 04 two-agent negotiation experiment.

The model, temperature, scenarios, role rules, and turn limit stay fixed across
conditions. Only the format paragraph and protocol reader change.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from protocol import CONDITIONS, FORMAT_PARAGRAPHS, ParsedMessage, parse_message


RESULT_HEADER = [
    "run",
    "condition",
    "scenario",
    "deal_possible",
    "outcome",
    "price",
    "correct",
    "violation",
    "turns",
    "format_errors",
    "reader_calls",
    "note",
]
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TURNS = 8
DEFAULT_MAX_RETRIES = 5
ModelCall = Callable[[str, list[dict[str, str]]], str]
LogCall = Callable[[str], None]


@dataclass(frozen=True)
class Scenario:
    id: str
    item: str
    reserve: int
    budget: int

    @property
    def deal_possible(self) -> bool:
        return self.reserve <= self.budget


@dataclass(frozen=True)
class PublicMessage:
    speaker: str
    raw: str


@dataclass(frozen=True)
class EpisodeResult:
    outcome: str
    price: int | None
    correct: int
    violation: int
    turns: int
    format_errors: int
    reader_calls: int
    note: str = ""


def load_scenarios(path: Path) -> list[Scenario]:
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = [Scenario(**item) for item in data]
    if not scenarios:
        raise ValueError("scenarios.json is empty")
    if len({scenario.id for scenario in scenarios}) != len(scenarios):
        raise ValueError("scenario ids must be unique")
    return scenarios


def agent_system_prompt(role: str, scenario: Scenario, condition: str) -> str:
    """Build invariant role rules plus the condition's only changed paragraph."""
    if role == "buyer":
        private_rule = (
            f"Your private maximum budget is {scenario.budget}. Never propose or "
            "accept a price above it. Do not reveal this private limit."
        )
        strategy = (
            "You open the negotiation. Make a concrete integer offer rather than "
            "asking for the seller's price."
        )
    elif role == "seller":
        private_rule = (
            f"Your private reserve price is {scenario.reserve}. Never propose or "
            "accept a price below it. Do not reveal this private limit."
        )
        strategy = "Respond to the buyer's latest message."
    else:
        raise ValueError(f"unknown role: {role}")

    return (
        f"You are the {role} negotiating the price of a {scenario.item}.\n"
        f"{private_rule}\n"
        f"{strategy}\n"
        "Use exactly one of four communication acts each turn: propose an integer "
        "price; accept-proposal to accept the other party's latest price; "
        "reject-proposal to decline while continuing; or refuse to leave with no "
        "deal. Accept only an actual proposal from the other party. Keep the "
        "message concise and do not invent facts about the item.\n"
        f"Output format for this condition: {FORMAT_PARAGRAPHS[condition]}"
    )


def conversation_for(role: str, transcript: Iterable[PublicMessage]) -> list[dict[str, str]]:
    """Present public messages as the role's assistant/user conversation."""
    messages: list[dict[str, str]] = []
    for message in transcript:
        messages.append({
            "role": "assistant" if message.speaker == role else "user",
            "content": message.raw,
        })
    if not messages:
        messages.append({
            "role": "user",
            "content": "Open the negotiation now with your first message.",
        })
    elif messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": "Continue the negotiation."})
    return messages


def calculate_result(scenario: Scenario, outcome: str, price: int | None,
                     turns: int, format_errors: int, reader_calls: int,
                     notes: list[str]) -> EpisodeResult:
    violation = int(
        outcome == "deal"
        and price is not None
        and (price < scenario.reserve or price > scenario.budget)
    )
    correct = int(
        (
            scenario.deal_possible
            and outcome == "deal"
            and price is not None
            and not violation
        )
        or (not scenario.deal_possible and outcome == "no_deal")
    )
    return EpisodeResult(
        outcome=outcome,
        price=price,
        correct=correct,
        violation=violation,
        turns=turns,
        format_errors=format_errors,
        reader_calls=reader_calls,
        note="; ".join(notes),
    )


def run_episode(condition: str, scenario: Scenario, model_call: ModelCall,
                max_turns: int, log: LogCall) -> EpisodeResult:
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if max_turns < 1:
        raise ValueError("max_turns must be positive")

    transcript: list[PublicMessage] = []
    last_proposal: tuple[str, int] | None = None
    format_errors = 0
    reader_calls = 0
    notes: list[str] = []

    def reader_call(system_prompt: str, raw_message: str) -> str:
        return model_call(system_prompt, [{"role": "user", "content": raw_message}])

    log(
        f"[episode] scenario={scenario.id} item={json.dumps(scenario.item)} "
        f"reserve={scenario.reserve} budget={scenario.budget} "
        f"deal_possible={int(scenario.deal_possible)}"
    )

    for turn in range(1, max_turns + 1):
        speaker = "buyer" if turn % 2 else "seller"
        raw = model_call(
            agent_system_prompt(speaker, scenario, condition),
            conversation_for(speaker, transcript),
        ).strip()
        transcript.append(PublicMessage(speaker, raw))
        log(
            f"[message] turn={turn} speaker={speaker} "
            f"raw={json.dumps(raw, ensure_ascii=False)}"
        )

        parsed: ParsedMessage = parse_message(condition, raw, reader_call)
        reader_calls += parsed.reader_calls
        if parsed.reader_calls:
            log(
                f"[reader] turn={turn} calls={parsed.reader_calls} "
                f"raw={json.dumps(parsed.reader_raw, ensure_ascii=False)}"
            )
        log(
            f"[parse] turn={turn} valid={str(parsed.valid).lower()} "
            f"performative={parsed.performative or ''} "
            f"price={'' if parsed.price is None else parsed.price} "
            f"error={json.dumps(parsed.error, ensure_ascii=False)}"
        )

        if not parsed.valid:
            format_errors += 1
            continue

        if parsed.performative == "propose":
            last_proposal = (speaker, int(parsed.price))
            continue

        if parsed.performative == "accept-proposal":
            if last_proposal is None or last_proposal[0] == speaker:
                note = f"turn {turn}: acceptance without other-party proposal"
                notes.append(note)
                log(f"[protocol-error] {note}")
                continue
            result = calculate_result(
                scenario, "deal", last_proposal[1], turn,
                format_errors, reader_calls, notes,
            )
            log(f"[result] {json.dumps(asdict(result), ensure_ascii=False)}")
            return result

        if parsed.performative == "refuse":
            result = calculate_result(
                scenario, "no_deal", None, turn,
                format_errors, reader_calls, notes,
            )
            log(f"[result] {json.dumps(asdict(result), ensure_ascii=False)}")
            return result

        # reject-proposal is valid and deliberately keeps the episode open.

    result = calculate_result(
        scenario, "open", None, max_turns,
        format_errors, reader_calls, notes,
    )
    log(f"[result] {json.dumps(asdict(result), ensure_ascii=False)}")
    return result


class OpenAIModel:
    """Small OpenAI-compatible client with bounded exponential retry."""

    def __init__(self, model: str, temperature: float,
                 max_retries: int = DEFAULT_MAX_RETRIES):
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        from openai import OpenAI

        self.client = OpenAI()
        self.model = model
        self.temperature = temperature
        self.max_retries = max_retries

    def __call__(self, system_prompt: str,
                 messages: list[dict[str, str]]) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": 240,
            "messages": [
                {"role": "system", "content": system_prompt},
                *messages,
            ],
            "timeout": 90.0,
        }
        if "openrouter.ai" in os.environ.get("OPENAI_BASE_URL", ""):
            request["extra_body"] = {"reasoning": {"enabled": False}}

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(**request)
                return response.choices[0].message.content or ""
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                retryable = status == 429 or (isinstance(status, int) and status >= 500)
                if not retryable or attempt >= self.max_retries:
                    raise
                wait_seconds = min(2 ** attempt, 30)
                print(
                    f"retryable model error ({type(exc).__name__}); "
                    f"waiting {wait_seconds}s",
                    flush=True,
                )
                time.sleep(wait_seconds)
        raise AssertionError("retry loop ended unexpectedly")


def ensure_results(path: Path) -> set[tuple[str, str, str]]:
    """Create/validate the CSV and return completed run-condition-scenario keys."""
    if not path.exists():
        with path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerow(RESULT_HEADER)
        return set()

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
        if handle.seekable():
            handle.seek(0)
            header = next(csv.reader(handle), [])
    if header != RESULT_HEADER:
        raise ValueError("results.csv has an unexpected header")
    return {(row["run"], row["condition"], row["scenario"]) for row in rows}


def append_result(path: Path, run_id: str, condition: str, scenario: Scenario,
                  result: EpisodeResult | None, note: str = "") -> None:
    if result is None:
        row: dict[str, Any] = {
            "run": run_id,
            "condition": condition,
            "scenario": scenario.id,
            "deal_possible": int(scenario.deal_possible),
            "outcome": "",
            "price": "",
            "correct": "",
            "violation": "",
            "turns": "",
            "format_errors": "",
            "reader_calls": "",
            "note": note,
        }
    else:
        row = {
            "run": run_id,
            "condition": condition,
            "scenario": scenario.id,
            "deal_possible": int(scenario.deal_possible),
            **asdict(result),
        }
        row["price"] = "" if result.price is None else result.price

    with path.open("a", encoding="utf-8", newline="") as handle:
        csv.DictWriter(handle, fieldnames=RESULT_HEADER).writerow(row)


def run_experiment(base: Path, conditions: Iterable[str], repeats: int,
                   max_turns: int, model_call: ModelCall) -> None:
    scenarios = load_scenarios(base / "scenarios.json")
    results_path = base / "results.csv"
    completed = ensure_results(results_path)
    logs_dir = base / "logs"
    logs_dir.mkdir(exist_ok=True)

    for condition in conditions:
        if condition not in CONDITIONS:
            raise ValueError(f"unknown condition: {condition}")
        for repeat in range(1, repeats + 1):
            run_id = f"{condition}-{repeat:02d}"
            log_path = logs_dir / f"{run_id}.txt"
            with log_path.open("a", encoding="utf-8") as log_handle:
                def log(line: str) -> None:
                    print(line, flush=True)
                    print(line, file=log_handle, flush=True)

                log(
                    f"[run] id={run_id} condition={condition} "
                    f"model={getattr(model_call, 'model', 'injected')} "
                    f"temperature={getattr(model_call, 'temperature', 'injected')} "
                    f"max_turns={max_turns}"
                )
                for scenario in scenarios:
                    key = (run_id, condition, scenario.id)
                    if key in completed:
                        log(f"[skip] completed scenario={scenario.id}")
                        continue
                    try:
                        result = run_episode(
                            condition, scenario, model_call, max_turns, log
                        )
                    except Exception as exc:
                        note = f"{type(exc).__name__}: {exc}"
                        log(f"[crash] scenario={scenario.id} error={json.dumps(note)}")
                        append_result(
                            results_path, run_id, condition, scenario, None, note
                        )
                        raise
                    append_result(results_path, run_id, condition, scenario, result)
                    completed.add(key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--conditions", nargs="+", choices=CONDITIONS,
        default=list(CONDITIONS),
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument(
        "--model", default=os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    )
    parser.add_argument(
        "--temperature", type=float,
        default=float(os.environ.get("AGENT_TEMPERATURE", DEFAULT_TEMPERATURE)),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.repeats < 1:
        raise SystemExit("--repeats must be positive")
    base = Path(__file__).resolve().parent
    model_call = OpenAIModel(args.model, args.temperature)
    run_experiment(
        base, args.conditions, args.repeats, args.max_turns, model_call
    )


if __name__ == "__main__":
    main()
