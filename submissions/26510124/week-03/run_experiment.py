#!/usr/bin/env python3
"""Run the required Contract Net experiment or the separate extension.

Required experiment (sequential bids, confidence-only award):
    python run_experiment.py base --runs 3 --harness react

Extension (concurrent bids, compare all three award policies):
    python run_experiment.py extended --runs 3 --condition overconfident

Every started run is appended. Failed attempts are data and are never removed.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_tools import TOOLSET_VERSION
from contract_net import POLICIES, ContractNetManager, RoundResult
from model_client import DEFAULT_MODEL, OpenAIBackend


ROOT = Path(__file__).resolve().parent
OFFICIAL_HEADER = [
    "run",
    "condition",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "note",
]
EXTENDED_HEADER = [
    "run",
    "condition",
    "award_policy",
    "harness",
    "tasks",
    "correct",
    "messages",
    "unassigned",
    "misawards",
    "parse_fails",
    "timeouts",
    "request_errors",
    "manager_tokens",
    "contractor_tokens",
    "monitor_tokens",
    "validation_cost",
    "validated_successes",
    "avg_reputation",
    "quality_per_1k_tokens",
    "note",
]


class JsonlLogger:
    """Flush every event so a crash still leaves an honest partial log."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = path.open("x", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self, event: dict[str, Any]) -> None:
        row = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        with self._lock:
            if self._closed:
                return
            line = json.dumps(row, ensure_ascii=False, sort_keys=True)
            print(line)
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._handle.close()
                self._closed = True


def load_tasks() -> list[dict[str, Any]]:
    value = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError("tasks.json must contain a non-empty list")
    return value


def _existing_run_numbers(path: Path) -> list[int]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        return [int(row["run"]) for row in rows if (row.get("run") or "").isdigit()]


def next_run_number(path: Path) -> int:
    numbers = _existing_run_numbers(path)
    return max(numbers, default=0) + 1


def append_row(path: Path, header: list[str], row: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8", newline="") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0, os.SEEK_END)
        is_empty = handle.tell() == 0
        writer = csv.writer(handle)
        if is_empty:
            writer.writerow(header)
        writer.writerow(list(row))
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def result_note(result: RoundResult, backend: OpenAIBackend, harness: str) -> str:
    return ";".join(
        [
            f"provider={backend.provider}",
            f"model={backend.model}",
            f"temperature={backend.temperature}",
            f"harness={harness}",
            f"parse_fails={result.parse_fails}",
            f"timeouts={result.timeouts}",
            f"request_errors={result.request_errors}",
            f"contractor_tokens={result.contractor_tokens}",
            f"validated={result.validated_successes}/{result.tasks - result.unassigned}",
            f"interventions={result.interventions}",
        ]
    )


def run_one(
    *,
    run_number: int,
    condition: str,
    policy: str,
    harness: str,
    async_bids: bool,
    log_path: Path,
    backend: OpenAIBackend,
    tasks: list[dict[str, Any]],
    bid_timeout: float,
    allow_write_tools: bool,
) -> tuple[RoundResult | None, str]:
    logger = JsonlLogger(log_path)
    logger(
        {
            "event": "run_config",
            "run": run_number,
            "condition": condition,
            "award_policy": policy,
            "harness": harness,
            "async_bids": async_bids,
            "provider": backend.provider,
            "model": backend.model,
            "temperature": backend.temperature,
            "bid_timeout_seconds": bid_timeout if async_bids else None,
            "toolset_version": TOOLSET_VERSION,
            "tools": [
                "calculator",
                "read_file",
                "count_pattern",
                "check_python",
                "write_note",
            ],
            "write_tools_approved": allow_write_tools,
            "uncontrolled_options": [
                "provider-side scheduling",
                "provider implementation details",
            ],
        }
    )
    try:
        manager = ContractNetManager(
            backend,
            harness=harness,
            bid_timeout_seconds=bid_timeout,
            allow_write_tools=allow_write_tools,
            log=logger,
        )
        result = manager.run_round(
            tasks,
            run_id=f"run{run_number:03d}",
            condition=condition,
            award_policy=policy,
            async_bids=async_bids,
        )
        logger(
            {
                "event": "run_summary",
                **result.__dict__,
                "quality_per_1k_tokens": result.quality_per_1k_tokens,
            }
        )
        return result, ""
    except Exception as exc:
        error = f"crash: {type(exc).__name__}: {exc}"
        logger({"event": "run_crash", "error": error})
        return None, error
    finally:
        logger.close()


def require_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "OPENAI_API_KEY is not set. Configure it in the environment; never put it in the repo."
        )


def run_base(args: argparse.Namespace) -> None:
    require_api_key()
    tasks = load_tasks()
    results_path = ROOT / "results.csv"
    backend = OpenAIBackend(args.model, args.temperature, args.request_timeout)
    conditions = [args.condition] if args.condition else ["baseline", "homogeneous", "overconfident"]
    run_number = next_run_number(results_path)
    for condition in conditions:
        for _ in range(args.runs):
            log_path = ROOT / "logs" / f"{condition}-run-{run_number:03d}.log"
            result, error = run_one(
                run_number=run_number,
                condition=condition,
                policy="confidence_only",
                harness=args.harness,
                async_bids=False,
                log_path=log_path,
                backend=backend,
                tasks=tasks,
                bid_timeout=args.bid_timeout,
                allow_write_tools=args.allow_write_tools,
            )
            if result is None:
                row = [run_number, condition, "", "", "", "", "", error]
            else:
                row = [
                    run_number,
                    condition,
                    result.tasks,
                    result.correct,
                    result.messages,
                    result.unassigned,
                    result.misawards,
                    result_note(result, backend, args.harness),
                ]
            append_row(results_path, OFFICIAL_HEADER, row)
            run_number += 1


def run_extended(args: argparse.Namespace) -> None:
    require_api_key()
    tasks = load_tasks()
    results_path = ROOT / "extended_results.csv"
    backend = OpenAIBackend(
        args.model,
        args.temperature,
        min(args.request_timeout, args.bid_timeout),
    )
    policies = [args.policy] if args.policy else list(POLICIES)
    run_number = next_run_number(results_path)
    for policy in policies:
        for _ in range(args.runs):
            log_path = ROOT / "extended_logs" / (
                f"{args.condition}-{policy}-run-{run_number:03d}.jsonl"
            )
            result, error = run_one(
                run_number=run_number,
                condition=args.condition,
                policy=policy,
                harness=args.harness,
                async_bids=True,
                log_path=log_path,
                backend=backend,
                tasks=tasks,
                bid_timeout=args.bid_timeout,
                allow_write_tools=args.allow_write_tools,
            )
            if result is None:
                row = [run_number, args.condition, policy, args.harness] + [""] * 15 + [error]
            else:
                row = [
                    run_number,
                    args.condition,
                    policy,
                    args.harness,
                    result.tasks,
                    result.correct,
                    result.messages,
                    result.unassigned,
                    result.misawards,
                    result.parse_fails,
                    result.timeouts,
                    result.request_errors,
                    result.manager_tokens,
                    result.contractor_tokens,
                    result.monitor_tokens,
                    result.validation_cost,
                    result.validated_successes,
                    json.dumps(result.avg_reputation, sort_keys=True),
                    "" if result.quality_per_1k_tokens is None else f"{result.quality_per_1k_tokens:.6f}",
                    result_note(result, backend, args.harness),
                ]
            append_row(results_path, EXTENDED_HEADER, row)
            run_number += 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="mode", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--runs", type=int, default=3)
        target.add_argument("--harness", choices=["react", "plan_execute"], default="react")
        target.add_argument("--model", default=DEFAULT_MODEL)
        target.add_argument("--temperature", type=float, default=0.2)
        target.add_argument("--request-timeout", type=float, default=90.0)
        target.add_argument("--bid-timeout", type=float, default=60.0)
        target.add_argument("--allow-write-tools", action="store_true")

    base = subparsers.add_parser("base", help="required sequential confidence-only experiment")
    common(base)
    base.add_argument("--condition", choices=["baseline", "homogeneous", "overconfident"])
    base.set_defaults(function=run_base)

    extended = subparsers.add_parser("extended", help="async token/reputation policy extension")
    common(extended)
    extended.add_argument(
        "--condition",
        choices=["baseline", "homogeneous", "overconfident"],
        default="overconfident",
    )
    extended.add_argument("--policy", choices=POLICIES)
    extended.set_defaults(function=run_extended)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if args.bid_timeout <= 0 or args.request_timeout <= 0:
        raise SystemExit("timeouts must be positive")
    args.function(args)


if __name__ == "__main__":
    main()
