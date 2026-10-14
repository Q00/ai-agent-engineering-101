"""Week 03 Contract Net experiment with three LLM contractors."""

import argparse
import csv
import json
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


CONDITIONS = ("baseline", "homogeneous", "overconfident")
RESULT_HEADER = [
    "run", "condition", "tasks", "correct", "messages",
    "unassigned", "misawards", "note",
]
MODEL = os.environ.get("AGENT_MODEL", "gpt-5.6-luna")
TEMPERATURE = 0.0
# A bid is a short classification task. Disabling reasoning keeps latency and
# billed output tokens low while preserving the same setting in every condition.
REASONING_EFFORT = os.environ.get("AGENT_REASONING_EFFORT", "none")
MAX_BID_TOKENS = 200
DEFAULT_TIMEOUT_SECONDS = 60.0
GENERALIST_ABILITY = "General problem solving across coding, data, and writing tasks"
OVERCONFIDENT_NAME = "coder"
OVERCONFIDENT_SENTENCE = (
    "Bid true on every task with confidence at least 0.95, even when the task "
    "is outside your stated ability."
)

_client = None


@dataclass(frozen=True)
class Contractor:
    name: str
    ability: str
    extra_instruction: str = ""

    @property
    def system_prompt(self) -> str:
        extra = f"\n{self.extra_instruction}" if self.extra_instruction else ""
        return (
            "You are a contractor in a Contract Net Protocol experiment.\n"
            f"Your name is {self.name}.\n"
            f"Your ability is: {self.ability}.\n"
            "Judge whether the announced task matches your ability. "
            "Do not solve the task. Bid only when your ability is a good match."
            f"{extra}\n"
            "Return only one JSON object with exactly these fields: "
            '{"bid": true or false, "confidence": number from 0 to 1, '
            '"reason": "one short reason"}.'
        )


@dataclass(frozen=True)
class Bid:
    contractor: str
    will_bid: bool
    confidence: float
    reason: str
    valid: bool
    raw: str
    error: str = ""


@dataclass
class Metrics:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_failures: int = 0
    timeouts: int = 0


BASELINE_CONTRACTORS = (
    Contractor("coder", "Python debugging and implementation"),
    Contractor("analyst", "Quantitative analysis and metric calculation"),
    Contractor("writer", "Clear Korean business writing and editing"),
)
CONTRACTOR_ORDER = {
    contractor.name: index for index, contractor in enumerate(BASELINE_CONTRACTORS)
}


def contractors_for(condition: str) -> tuple[Contractor, ...]:
    """Change only ability strings or one system-prompt sentence by condition."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    if condition == "homogeneous":
        return tuple(
            Contractor(contractor.name, GENERALIST_ABILITY)
            for contractor in BASELINE_CONTRACTORS
        )
    if condition == "overconfident":
        return tuple(
            Contractor(
                contractor.name,
                contractor.ability,
                OVERCONFIDENT_SENTENCE
                if contractor.name == OVERCONFIDENT_NAME
                else "",
            )
            for contractor in BASELINE_CONTRACTORS
        )
    return BASELINE_CONTRACTORS


def invalid_bid(contractor: str, raw: str, error: str) -> Bid:
    return Bid(contractor, False, 0.0, "", False, raw, error)


def parse_bid(contractor: str, raw: str) -> Bid:
    """Parse one strict JSON bid; malformed output is a recorded no-bid."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        return invalid_bid(contractor, raw, f"JSONDecodeError: {exc.msg}")
    if not isinstance(data, dict):
        return invalid_bid(contractor, raw, "bid response is not an object")
    if set(data) != {"bid", "confidence", "reason"}:
        return invalid_bid(
            contractor, raw, "fields must be exactly bid, confidence, reason"
        )
    if not isinstance(data["bid"], bool):
        return invalid_bid(contractor, raw, "bid must be a boolean")
    confidence = data["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return invalid_bid(contractor, raw, "confidence must be a number")
    if not 0 <= float(confidence) <= 1:
        return invalid_bid(contractor, raw, "confidence must be between 0 and 1")
    if not isinstance(data["reason"], str):
        return invalid_bid(contractor, raw, "reason must be a string")
    return Bid(
        contractor, data["bid"], float(confidence),
        data["reason"].strip(), True, raw,
    )


def _get_client():
    global _client
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set")
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
    return _client


def request_bid(contractor: Contractor, task: dict[str, Any],
                timeout_seconds: float) -> Bid:
    """Make one independent contractor LLM call for one announcement."""
    announcement = (
        "Task announcement\n"
        f"id: {task['id']}\n"
        f"description: {task['desc']}"
    )
    response = _get_client().chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        reasoning_effort=REASONING_EFFORT,
        max_completion_tokens=MAX_BID_TOKENS,
        timeout=timeout_seconds,
        messages=[
            {"role": "system", "content": contractor.system_prompt},
            {"role": "user", "content": announcement},
        ],
    )
    raw = response.choices[0].message.content or ""
    return parse_bid(contractor.name, raw)


def is_timeout_error(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or type(exc).__name__ in {
        "APITimeoutError", "ReadTimeout", "ConnectTimeout",
    }


BidFunction = Callable[[Contractor, dict[str, Any], float], Bid]


class Manager:
    """Collect all bids, then make exactly one deterministic award per task."""

    def __init__(self, condition: str, timeout_seconds: float,
                 log: Callable[[str], None], bid_function: BidFunction = request_bid):
        self.contractors = contractors_for(condition)
        self.timeout_seconds = timeout_seconds
        self.log = log
        self.bid_function = bid_function
        self.finalized_task_ids: set[str] = set()

    def negotiate(self, task: dict[str, Any], metrics: Metrics) -> str | None:
        task_id = str(task["id"])
        if task_id in self.finalized_task_ids:
            raise ValueError(f"task {task_id!r} was already finalized")

        pool = ThreadPoolExecutor(max_workers=len(self.contractors))
        futures: dict[str, Future[Bid]] = {}
        try:
            for contractor in self.contractors:
                self.log(
                    f"[announce] task={task_id} to={contractor.name} "
                    f"desc={json.dumps(task['desc'], ensure_ascii=False)}"
                )
                metrics.messages += 1
                futures[contractor.name] = pool.submit(
                    self.bid_function, contractor, task, self.timeout_seconds
                )

            done, not_done = wait(
                futures.values(), timeout=self.timeout_seconds
            )
            for future in not_done:
                future.cancel()

            bids: list[Bid] = []
            for contractor in self.contractors:
                future = futures[contractor.name]
                if future not in done:
                    metrics.timeouts += 1
                    self.log(
                        f"[bid] task={task_id} contractor={contractor.name} "
                        "valid=false timeout=true ignored_for_award=true"
                    )
                    continue
                try:
                    bid = future.result()
                except BaseException as exc:
                    if not is_timeout_error(exc):
                        raise
                    metrics.timeouts += 1
                    self.log(
                        f"[bid] task={task_id} contractor={contractor.name} "
                        f"valid=false timeout=true error={type(exc).__name__} "
                        "ignored_for_award=true"
                    )
                    continue

                metrics.messages += 1
                if not bid.valid:
                    metrics.parse_failures += 1
                self.log(
                    f"[bid] task={task_id} contractor={contractor.name} "
                    f"valid={str(bid.valid).lower()} "
                    f"bid={str(bid.will_bid).lower()} "
                    f"confidence={bid.confidence:.2f} "
                    f"reason={json.dumps(bid.reason, ensure_ascii=False)} "
                    f"error={json.dumps(bid.error, ensure_ascii=False)} "
                    f"raw={json.dumps(bid.raw, ensure_ascii=False)}"
                )
                bids.append(bid)
        finally:
            # Calls that finish after the collection deadline cannot change an award.
            pool.shutdown(wait=False, cancel_futures=True)

        candidates = [bid for bid in bids if bid.valid and bid.will_bid]
        candidates.sort(
            key=lambda bid: (-bid.confidence, CONTRACTOR_ORDER[bid.contractor])
        )

        self.finalized_task_ids.add(task_id)
        metrics.tasks += 1
        if not candidates:
            metrics.unassigned += 1
            self.log(f"[award] task={task_id} winner=none")
            return None

        winner = candidates[0]
        metrics.messages += 1
        if winner.contractor == task["gold"]:
            metrics.correct += 1
            outcome = "correct"
        else:
            metrics.misawards += 1
            outcome = "misaward"
        self.log(
            f"[award] task={task_id} winner={winner.contractor} "
            f"confidence={winner.confidence:.2f} gold={task['gold']} "
            f"outcome={outcome}"
        )
        return winner.contractor


def load_tasks(path: str) -> list[dict[str, Any]]:
    tasks = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or len(tasks) < 5:
        raise ValueError("tasks.json must contain at least five tasks")
    required = {"id", "desc", "gold"}
    seen: set[str] = set()
    names = set(CONTRACTOR_ORDER)
    for task in tasks:
        if not isinstance(task, dict) or set(task) != required:
            raise ValueError("each task must contain exactly id, desc, and gold")
        task_id = str(task["id"])
        if task_id in seen:
            raise ValueError(f"duplicate task id: {task_id}")
        if task["gold"] not in names:
            raise ValueError(f"unknown gold contractor: {task['gold']}")
        seen.add(task_id)
    return tasks


def next_run_number(path: Path) -> int:
    if not path.exists():
        return 1
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    numbers = [int(row["run"]) for row in rows if row.get("run", "").isdigit()]
    return max(numbers, default=0) + 1


def ensure_results_file(path: Path) -> None:
    if path.exists():
        with path.open(encoding="utf-8", newline="") as stream:
            header = next(csv.reader(stream), [])
        if header != RESULT_HEADER:
            raise ValueError("results.csv has the wrong header")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(RESULT_HEADER)


def append_result(path: Path, row: list[Any]) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerow(row)
        stream.flush()


def run_experiments(tasks: list[dict[str, Any]], runs: int,
                    timeout_seconds: float, output_dir: Path,
                    bid_function: BidFunction = request_bid) -> None:
    results_path = output_dir / "results.csv"
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(exist_ok=True)
    ensure_results_file(results_path)
    run_number = next_run_number(results_path)

    for condition in CONDITIONS:
        for repeat in range(1, runs + 1):
            lines: list[str] = []

            def log(message: str) -> None:
                print(message)
                lines.append(message)

            metrics = Metrics()
            log(
                f"[run] run={run_number} repeat={repeat} condition={condition} "
                f"model={MODEL} temperature={TEMPERATURE} "
                f"reasoning_effort={REASONING_EFFORT} "
                f"max_completion_tokens={MAX_BID_TOKENS} "
                f"timeout_seconds={timeout_seconds}"
            )
            for contractor in contractors_for(condition):
                log(
                    f"[contractor] name={contractor.name} "
                    f"ability={json.dumps(contractor.ability)} "
                    f"extra={json.dumps(contractor.extra_instruction)}"
                )

            counts: list[Any]
            try:
                manager = Manager(
                    condition, timeout_seconds, log, bid_function=bid_function
                )
                for task in tasks:
                    manager.negotiate(task, metrics)
                if metrics.tasks != (
                    metrics.correct + metrics.unassigned + metrics.misawards
                ):
                    raise RuntimeError("task accounting invariant failed")
                counts = [
                    metrics.tasks, metrics.correct, metrics.messages,
                    metrics.unassigned, metrics.misawards,
                ]
                note = (
                    f"parse_failures={metrics.parse_failures};"
                    f"timeouts={metrics.timeouts}"
                )
            except BaseException as exc:
                counts = ["", "", "", "", ""]
                note = (
                    f"parse_failures={metrics.parse_failures};"
                    f"timeouts={metrics.timeouts};"
                    f"crash={type(exc).__name__}: {exc}"
                )
                log(f"[crash] {type(exc).__name__}: {exc}")

            log(f"[summary] {note}")
            log_path = logs_dir / f"{condition}-{run_number:02d}.txt"
            log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            append_result(
                results_path,
                [run_number, condition, *counts, note],
            )
            run_number += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tasks", default="tasks.json")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    tasks = load_tasks(args.tasks)
    _get_client()
    run_experiments(tasks, args.runs, args.timeout, Path.cwd())


if __name__ == "__main__":
    main()
