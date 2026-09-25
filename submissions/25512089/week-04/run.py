from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

BASE = Path(__file__).resolve().parent
SCENARIOS_FILE = BASE / "scenarios.json"
RESULTS_FILE = BASE / "results.csv"
LOG_DIR = BASE / "logs"

MODEL = os.getenv("AGENT_MODEL", "")
TEMPERATURE = 0.0
TURN_LIMIT = 4

HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note"
]

CONDITIONS = ("free", "tagged", "structured")
PERFORMATIVES = ("propose", "accept-proposal", "reject-proposal", "refuse")

FORMAT_PARAGRAPHS = {
    "free": """
Reply in plain English only.
Do not write performative labels, tags, JSON, or metadata.
Express exactly one negotiation action in a short natural-language message.
""".strip(),

    "tagged": """
Reply with exactly one FIPA-style performative tag in parentheses,
followed by a short plain-English message.
Allowed tags are:
(propose), (accept-proposal), (reject-proposal), (refuse)
Example: (propose) I can offer 75.
""".strip(),

    "structured": """
Reply with exactly one JSON object and nothing else:
{"performative":"propose","content":{"price":75}}
Allowed performatives are:
propose, accept-proposal, reject-proposal, refuse.
For non-propose acts, use:
{"performative":"refuse","content":{"price":null}}
""".strip(),
}

READER_PROMPT = """
You are a protocol reader for a price negotiation.

Classify the message into exactly one of:
propose, accept-proposal, reject-proposal, refuse.

If the message proposes a concrete price, extract that integer.
Otherwise price must be null.

Return ONLY one JSON object:
{"performative":"propose","price":75}
or
{"performative":"refuse","price":null}

Do not explain your reasoning.
""".strip()


class DailyRateLimit(RuntimeError):
    pass


def client() -> OpenAI:
    return OpenAI()


def model_call(messages: list[dict[str, str]], max_tokens: int = 160) -> str:
    if not MODEL:
        raise RuntimeError("AGENT_MODEL is not set")

    waits = [2, 4, 8, 16]

    for attempt in range(len(waits) + 1):
        try:
            response = client().chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
                extra_body={"reasoning": {"enabled": False}},
            )
            return (response.choices[0].message.content or "").strip()

        except Exception as e:
            text = str(e)

            if "429" in text and "free-models-per-day" in text:
                raise DailyRateLimit(text) from e

            if "429" in text and attempt < len(waits):
                wait = waits[attempt]
                print(f"[retry] HTTP 429; waiting {wait}s")
                time.sleep(wait)
                continue

            raise


def parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()

    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def extract_integer(text: str) -> int | None:
    match = re.search(r"(?<!\d)(\d+)(?!\d)", text)
    return int(match.group(1)) if match else None


def read_free(text: str) -> tuple[str | None, int | None, int]:
    raw = model_call([
        {"role": "system", "content": READER_PROMPT},
        {"role": "user", "content": text},
    ])

    data = parse_json_object(raw)
    if not data:
        return None, None, 1

    perf = data.get("performative")
    price = data.get("price")

    if perf not in PERFORMATIVES:
        return None, None, 1

    if isinstance(price, int):
        return perf, price, 1

    return perf, None, 1


def read_tagged(text: str) -> tuple[str | None, int | None, int, int]:
    match = re.match(
        r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)",
        text,
        re.I,
    )

    if not match:
        return None, None, 0, 1

    perf = match.group(1).lower()

    if perf != "propose":
        return perf, None, 0, 0

    reader_prompt = """
Extract only the proposed integer price from this negotiation message.
Return ONLY:
{"price":75}
If no concrete integer price exists, return:
{"price":null}
""".strip()

    raw = model_call([
        {"role": "system", "content": reader_prompt},
        {"role": "user", "content": text},
    ])

    data = parse_json_object(raw)

    if not data:
        return perf, None, 1, 1

    price = data.get("price")
    if isinstance(price, int):
        return perf, price, 1, 0

    return perf, None, 1, 1


def read_structured(text: str) -> tuple[str | None, int | None, int]:
    data = parse_json_object(text)

    if not data:
        return None, None, 1

    perf = data.get("performative")
    content = data.get("content")

    if perf not in PERFORMATIVES or not isinstance(content, dict):
        return None, None, 1

    price = content.get("price")

    if perf == "propose":
        if not isinstance(price, int):
            return None, None, 1
        return perf, price, 0

    return perf, None, 0


def role_prompt(
    role: str,
    item: str,
    private_limit: int,
    condition: str,
) -> str:
    if role == "buyer":
        private_text = (
            f"You are the buyer negotiating for {item}. "
            f"Your PRIVATE maximum budget is {private_limit}. "
            "Never knowingly agree to a price above your budget."
        )
    else:
        private_text = (
            f"You are the seller negotiating {item}. "
            f"Your PRIVATE reserve price is {private_limit}. "
            "Never knowingly agree to a price below your reserve."
        )

    strategy = """
Negotiate briefly and realistically.
Allowed communicative acts are only:
propose, accept-proposal, reject-proposal, refuse.

If you make or counter an offer, use propose with one integer price.
Use accept-proposal only to accept the other side's latest proposed price.
Use refuse when you want to leave permanently.
Do not reveal your private limit.
""".strip()

    return f"{private_text}\n\n{strategy}\n\n{FORMAT_PARAGRAPHS[condition]}"


def call_agent(
    role: str,
    item: str,
    private_limit: int,
    condition: str,
    transcript: list[str],
    opening: bool,
) -> str:
    system = role_prompt(role, item, private_limit, condition)

    if opening:
        instruction = (
            "You are the buyer and must open the negotiation now. "
            "Produce exactly one short opening message appropriate for a buyer."
        )
    else:
        instruction = (
            "Continue the negotiation based on the transcript. "
            "Produce exactly one next message."
        )

    transcript_text = "\n".join(transcript) if transcript else "(no previous messages)"

    user = f"""Negotiation transcript:
{transcript_text}

{instruction}
"""

    return model_call([
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])


def parse_message(
    condition: str,
    text: str,
) -> tuple[str | None, int | None, int, int]:
    if condition == "free":
        perf, price, reader_calls = read_free(text)
        format_error = 0 if perf else 1
        return perf, price, reader_calls, format_error

    if condition == "tagged":
        perf, price, reader_calls, format_error = read_tagged(text)
        return perf, price, reader_calls, format_error

    perf, price, format_error = read_structured(text)
    return perf, price, 0, format_error


def evaluate(
    reserve: int,
    budget: int,
    outcome: str,
    price: int | None,
) -> tuple[int, int]:
    possible = reserve <= budget

    violation = int(
        outcome == "deal"
        and price is not None
        and (price < reserve or price > budget)
    )

    if possible:
        correct = int(
            outcome == "deal"
            and price is not None
            and reserve <= price <= budget
        )
    else:
        correct = int(outcome in ("no_deal", "open"))

    return correct, violation


def load_scenarios() -> list[dict[str, Any]]:
    return json.loads(SCENARIOS_FILE.read_text(encoding="utf-8"))


def existing_success_keys() -> set[tuple[str, str]]:
    if not RESULTS_FILE.exists():
        return set()

    keys: set[tuple[str, str]] = set()

    with RESULTS_FILE.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("run", "").strip() and row.get("scenario", "").strip():
                keys.add((row["run"].strip(), row["scenario"].strip()))

    return keys


def append_result(row: dict[str, Any]) -> None:
    with RESULTS_FILE.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writerow(row)


def run_id(condition: str, repeat: int) -> str:
    base = {"free": 0, "tagged": 3, "structured": 6}[condition]
    return str(base + repeat)


def run_episode(
    scenario: dict[str, Any],
    condition: str,
    log,
) -> dict[str, Any]:
    item = scenario["item"]
    reserve = scenario["reserve"]
    budget = scenario["budget"]
    possible = int(reserve <= budget)

    transcript: list[str] = []
    last_proposal: int | None = None
    last_proposer: str | None = None

    outcome = "open"
    deal_price: int | None = None
    reader_calls = 0
    format_errors = 0
    turns = 0

    speaker = "buyer"

    for turn in range(1, TURN_LIMIT + 1):
        opening = turn == 1

        limit = budget if speaker == "buyer" else reserve

        raw = call_agent(
            role=speaker,
            item=item,
            private_limit=limit,
            condition=condition,
            transcript=transcript,
            opening=opening,
        )

        turns += 1
        line = f"{speaker.upper()}: {raw}"
        transcript.append(line)

        print(line)
        log.write(line + "\n")

        perf, price, rcalls, ferr = parse_message(condition, raw)
        reader_calls += rcalls
        format_errors += ferr

        parsed = (
            f"PARSED: performative={perf} price={price} "
            f"format_error={ferr} reader_calls+={rcalls}"
        )
        print(parsed)
        log.write(parsed + "\n")

        if perf is None:
            speaker = "seller" if speaker == "buyer" else "buyer"
            continue

        if perf == "propose":
            if price is not None:
                last_proposal = price
                last_proposer = speaker

        elif perf == "accept-proposal":
            if (
                last_proposal is not None
                and last_proposer is not None
                and last_proposer != speaker
            ):
                outcome = "deal"
                deal_price = last_proposal
                break
            else:
                format_errors += 1

        elif perf == "refuse":
            outcome = "no_deal"
            break

        elif perf == "reject-proposal":
            pass

        speaker = "seller" if speaker == "buyer" else "buyer"

    correct, violation = evaluate(
        reserve=reserve,
        budget=budget,
        outcome=outcome,
        price=deal_price,
    )

    result_line = (
        f"RESULT: outcome={outcome} price={deal_price} correct={correct} "
        f"violation={violation} turns={turns} "
        f"format_errors={format_errors} reader_calls={reader_calls}"
    )
    print(result_line)
    log.write(result_line + "\n")

    return {
        "deal_possible": possible,
        "outcome": outcome,
        "price": "" if deal_price is None else deal_price,
        "correct": correct,
        "violation": violation,
        "turns": turns,
        "format_errors": format_errors,
        "reader_calls": reader_calls,
        "note": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", required=True, choices=CONDITIONS)
    parser.add_argument("--repeat", required=True, type=int, choices=(1, 2, 3))
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    rid = run_id(args.condition, args.repeat)
    logfile = LOG_DIR / f"run-{rid}-{args.condition}-repeat-{args.repeat}.txt"

    done = existing_success_keys()
    scenarios = load_scenarios()

    with logfile.open("a", encoding="utf-8") as log:
        log.write(
            f"\nRUN {rid} condition={args.condition} repeat={args.repeat} "
            f"model={MODEL} temperature={TEMPERATURE} turn_limit={TURN_LIMIT}\n"
        )

        for scenario in scenarios:
            sid = str(scenario["id"])
            key = (rid, sid)

            if key in done:
                print(f"[skip] run={rid} scenario={sid} already complete")
                log.write(f"[skip] scenario={sid} already complete\n")
                continue

            print(f"\n=== run={rid} condition={args.condition} scenario={sid} ===")
            log.write(
                f"\n=== scenario={sid} item={scenario['item']} "
                f"reserve={scenario['reserve']} budget={scenario['budget']} ===\n"
            )

            try:
                metrics = run_episode(scenario, args.condition, log)

                row = {
                    "run": rid,
                    "condition": args.condition,
                    "scenario": sid,
                    **metrics,
                }
                append_result(row)

            except DailyRateLimit as e:
                log.write(f"INTERRUPTED: daily rate limit: {e}\n")
                print("\n[stopped] OpenRouter daily free-model limit reached.")
                print("Run the SAME command after the quota resets; completed scenarios will be skipped.")
                return

            except Exception as e:
                note = f"crash: {type(e).__name__}: {e}"
                log.write(note + "\n")

                append_result({
                    "run": rid,
                    "condition": args.condition,
                    "scenario": sid,
                    "deal_possible": "",
                    "outcome": "",
                    "price": "",
                    "correct": "",
                    "violation": "",
                    "turns": "",
                    "format_errors": "",
                    "reader_calls": "",
                    "note": note,
                })

                print(note)

    print(f"\n[saved] {logfile.name}")


if __name__ == "__main__":
    main()
