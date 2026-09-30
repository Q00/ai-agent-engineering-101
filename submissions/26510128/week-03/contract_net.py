"""Week 03 — Contract Net Protocol with three LLM contractors.

One manager announces every task to A, B, and C. Each contractor is one
independent LLM call with its own system prompt. The manager awards the task to
the highest-confidence valid bidder. Three experimental conditions are run with
the same task set, model, temperature, and common prompt template.

Usage:
    python contract_net.py --runs 3
    python contract_net.py --condition baseline --runs 1

Environment:
    OPENAI_BASE_URL=https://openrouter.ai/api/v1
    OPENAI_API_KEY=<secret>
    AGENT_MODEL=nvidia/nemotron-3.5-lightning:free
    AGENT_TEMPERATURE=0.2
    AGENT_MAX_TOKENS=220
    AGENT_REQUEST_INTERVAL=3.2
    AGENT_MAX_RETRIES=2
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI, RateLimitError


class ExperimentPaused(Exception):
    """Stop the entire run when the provider rejects requests for quota/rate."""



BASE_DIR = Path(__file__).resolve().parent
TASKS_PATH = BASE_DIR / "tasks.json"
RESULTS_PATH = BASE_DIR / "results.csv"
LOGS_DIR = BASE_DIR / "logs"

HEADER = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]
CONDITIONS = ("baseline", "homogeneous", "overconfident")

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "220"))
REQUEST_INTERVAL = float(os.environ.get("AGENT_REQUEST_INTERVAL", "3.2"))
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "2"))

BASE_SKILLS = {
    "A": "numerical calculation, arithmetic, algebra, and quantitative word problems",
    "B": "natural-language writing, editing, tone, and concise prose",
    "C": "programming, debugging, and code-related tasks",
}
GENERALIST_SKILL = "general problem solving across calculation, writing, and programming"

COMMON_SYSTEM = (
    "You are contractor {name} in a Contract Net Protocol. "
    "Your assigned skill is: {skill}. "
    "You receive one task announcement and must decide whether to bid. "
    "Bid only when the task falls inside your assigned skill. "
    "If you bid, confidence must reflect how well the task matches your assigned skill. "
    "Submit exactly one bid through the submit_bid function. "
    "Do not answer the task itself."
)
OVERCONFIDENT_SUFFIX = (
    " You are certain you can do any task well. Always bid, with confidence 95 or higher."
)

BID_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_bid",
        "description": "Submit this contractor's bid decision for the announced task.",
        "parameters": {
            "type": "object",
            "properties": {
                "bid": {
                    "type": "boolean",
                    "description": "True if the contractor bids for the task.",
                },
                "confidence": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Confidence that this task matches the assigned skill.",
                },
                "reason": {
                    "type": "string",
                    "description": "One short sentence explaining the bid decision.",
                },
            },
            "required": ["bid", "confidence", "reason"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class Bid:
    contractor: str
    bid: bool
    confidence: int
    reason: str
    raw: str
    parse_error: str = ""


def get_client() -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=key, base_url=base_url)
    return OpenAI(api_key=key)


def system_prompt(contractor: str, condition: str) -> str:
    if condition == "homogeneous":
        skill = GENERALIST_SKILL
    else:
        skill = BASE_SKILLS[contractor]

    prompt = COMMON_SYSTEM.format(name=contractor, skill=skill)
    if condition == "overconfident" and contractor == "C":
        prompt += OVERCONFIDENT_SUFFIX
    return prompt


def announcement(task: dict[str, Any]) -> str:
    return (
        "TASK-ANNOUNCEMENT\n"
        f"name: {task['id']}\n"
        f"task-abstraction: {task['desc']}\n"
        "eligibility-specification: available contractor\n"
        "bid-specification: bid, confidence, reason\n"
        "expiration-time: immediate"
    )


def validate_bid_object(contractor: str, obj: Any, raw: str) -> Bid:
    if not isinstance(obj, dict):
        return Bid(contractor, False, 0, "response was not a JSON object", raw, "schema")

    if not isinstance(obj.get("bid"), bool):
        return Bid(contractor, False, 0, "missing/invalid bid field", raw, "schema")

    confidence = obj.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return Bid(contractor, False, 0, "missing/invalid confidence field", raw, "schema")

    confidence = int(round(float(confidence)))
    if not 0 <= confidence <= 100:
        return Bid(contractor, False, 0, "confidence outside 0-100", raw, "schema")

    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return Bid(contractor, False, 0, "missing/invalid reason field", raw, "schema")

    return Bid(contractor, obj["bid"], confidence, reason.strip(), raw)


def parse_bid(contractor: str, raw: str) -> Bid:
    """Fallback parser for providers that return JSON in message content."""
    text = (raw or "").strip()

    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    # First try the entire reply.
    try:
        return validate_bid_object(contractor, json.loads(text), raw)
    except Exception:
        pass

    # Some reasoning models put prose before/after the JSON. Scan for a JSON
    # object without silently inventing any fields.
    decoder = json.JSONDecoder()
    for pos, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[pos:])
            return validate_bid_object(contractor, obj, raw)
        except Exception:
            continue

    return Bid(contractor, False, 0, "unparseable response", raw, "JSONDecodeError")


def ask_contractor(
    client: OpenAI,
    contractor: str,
    condition: str,
    task: dict[str, Any],
) -> Bid:
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system_prompt(contractor, condition)},
                    {"role": "user", "content": announcement(task)},
                ],
                tools=[BID_TOOL],
                tool_choice={
                    "type": "function",
                    "function": {"name": "submit_bid"},
                },
                extra_body={"reasoning": {"enabled": False}},
            )

            message = response.choices[0].message
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                call = tool_calls[0]
                raw_args = call.function.arguments or ""
                try:
                    obj = json.loads(raw_args)
                except Exception:
                    return Bid(
                        contractor,
                        False,
                        0,
                        "unparseable tool arguments",
                        raw_args,
                        "JSONDecodeError",
                    )
                return validate_bid_object(contractor, obj, raw_args)

            # Fallback in case a provider ignores forced tool calling.
            raw = message.content or ""
            return parse_bid(contractor, raw)

        except RateLimitError as exc:
            # Continuing with the other contractors would turn a quota outage
            # into an entire row of misleading no-bids. Do not print the
            # provider's exception text because it may include sensitive data.
            raise ExperimentPaused("OpenRouter RateLimitError (HTTP 429)") from exc

        except Exception as exc:
            return Bid(
                contractor=contractor,
                bid=False,
                confidence=0,
                reason=f"model call failed: {type(exc).__name__}",
                raw="",
                parse_error=f"api:{type(exc).__name__}",
            )

    raise AssertionError("unreachable")


def run_once(condition: str, tasks: list[dict[str, Any]], log) -> dict[str, int | str]:
    client = get_client()

    correct = 0
    messages = 0
    unassigned = 0
    misawards = 0
    parse_errors = 0
    api_errors = 0

    log(
        f"[RUN] condition={condition} model={MODEL} "
        f"temperature={TEMPERATURE} max_tokens={MAX_TOKENS} "
        f"request_interval={REQUEST_INTERVAL}"
    )

    first_request = True

    for task in tasks:
        log("")
        log(
            f"[TASK] id={task['id']} gold={task['gold']} "
            f"desc={task['desc']}"
        )

        # One announcement is sent to each of the three contractors.
        for contractor in ("A", "B", "C"):
            messages += 1
            log(f"[ANNOUNCE] manager -> {contractor}: {task['id']}")

        # Run contractor calls sequentially. OpenRouter's free endpoints are
        # rate-limited, so bursting three concurrent requests makes the
        # experiment less reproducible.
        bids: list[Bid] = []
        for contractor in ("A", "B", "C"):
            if not first_request and REQUEST_INTERVAL > 0:
                time.sleep(REQUEST_INTERVAL)
            first_request = False
            bids.append(ask_contractor(client, contractor, condition, task))

        valid_bidders: list[Bid] = []
        for bid in bids:
            if bid.parse_error:
                parse_errors += 1
                if bid.parse_error.startswith("api:"):
                    api_errors += 1
                raw_preview = bid.raw.replace("\n", " ")[:180]
                log(
                    f"[BID] {bid.contractor} -> NO BID "
                    f"parse_error={bid.parse_error} raw={raw_preview!r}"
                )
                continue

            log(
                f"[BID] {bid.contractor}: bid={str(bid.bid).lower()} "
                f"confidence={bid.confidence} reason={bid.reason}"
            )
            if bid.bid:
                messages += 1
                valid_bidders.append(bid)

        if not valid_bidders:
            unassigned += 1
            log("[AWARD] none (no valid bidder)")
            continue

        # Deterministic tie-break: higher confidence first, then A < B < C.
        winner = sorted(
            valid_bidders,
            key=lambda b: (-b.confidence, b.contractor),
        )[0]
        messages += 1
        log(
            f"[AWARD] manager -> {winner.contractor}: {task['id']} "
            f"confidence={winner.confidence} gold={task['gold']}"
        )

        if winner.contractor == task["gold"]:
            correct += 1
        else:
            misawards += 1

    note = f"parse_errors={parse_errors};api_errors={api_errors}"
    return {
        "tasks": len(tasks),
        "correct": correct,
        "messages": messages,
        "unassigned": unassigned,
        "misawards": misawards,
        "note": note,
    }


def load_tasks() -> list[dict[str, Any]]:
    tasks = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    if not isinstance(tasks, list):
        raise ValueError("tasks.json must contain a JSON list")
    return tasks


def existing_run_number() -> int:
    if not RESULTS_PATH.exists():
        return 0
    with RESULTS_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) <= 1:
        return 0
    nums = []
    for row in rows[1:]:
        try:
            nums.append(int(row[0]))
        except (ValueError, IndexError):
            pass
    return max(nums, default=0)


def ensure_results_header() -> None:
    if RESULTS_PATH.exists():
        return
    with RESULTS_PATH.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(HEADER)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--condition",
        choices=("all",) + CONDITIONS,
        default="all",
        help="run all three conditions or only one condition",
    )
    args = parser.parse_args()

    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    tasks = load_tasks()
    LOGS_DIR.mkdir(exist_ok=True)
    ensure_results_header()

    conditions = CONDITIONS if args.condition == "all" else (args.condition,)
    run_no = existing_run_number()

    paused = False
    with RESULTS_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)

        for condition in conditions:
            for repeat in range(1, args.runs + 1):
                run_no += 1
                lines: list[str] = []

                def log(message: str) -> None:
                    print(message)
                    lines.append(str(message))

                log(
                    f"[START] run={run_no} condition={condition} "
                    f"repeat={repeat}/{args.runs}"
                )

                try:
                    metrics = run_once(condition, tasks, log)
                    row = [
                        run_no,
                        condition,
                        metrics["tasks"],
                        metrics["correct"],
                        metrics["messages"],
                        metrics["unassigned"],
                        metrics["misawards"],
                        metrics["note"],
                    ]
                except ExperimentPaused as exc:
                    note = "paused: HTTP 429 RateLimitError; partial run; retry later"
                    log(f"[PAUSED] {note}")
                    row = [run_no, condition, "", "", "", "", "", note]
                    paused = True
                except Exception as exc:
                    # Keep crash type; never log arbitrary API exception text,
                    # which can inadvertently contain credentials.
                    note = f"crash: {type(exc).__name__}"
                    log(f"[CRASH] {note}")
                    row = [run_no, condition, "", "", "", "", "", note]

                log(
                    "[SUMMARY] "
                    f"tasks={row[2]} correct={row[3]} messages={row[4]} "
                    f"unassigned={row[5]} misawards={row[6]} note={row[7]}"
                )

                log_path = LOGS_DIR / f"{condition}-{run_no:02d}.txt"
                log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                writer.writerow(row)
                f.flush()
                if paused:
                    log("[PAUSED] Stop now; do not send more API requests until the quota is available.")
                    break
            if paused:
                break

    print(f"\nresults.csv updated: {RESULTS_PATH}")
    print(f"logs written under: {LOGS_DIR}")
    if paused:
        raise SystemExit("Stopped after rate limit; partial run saved as a failed run.")


if __name__ == "__main__":
    main()
