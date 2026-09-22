#!/usr/bin/env python3
"""Run/resume the fixed experiment; retain every finished or crashed episode."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from model_client import DEFAULT_MODEL, OpenAIBackend
from negotiation import run_episode
from prompts import COMMON, CONDITIONS, FORMAT, OPENING_CUE, READER_SYSTEM, ROLE

ROOT = Path(__file__).resolve().parent
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]


def load_scenarios(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) < 4:
        raise ValueError("at least four scenarios required")
    seen = set()
    for s in value:
        if not isinstance(s, dict) or set(s) != {"id", "item", "reserve", "budget"}:
            raise ValueError("scenario fields must be id, item, reserve, budget")
        if not isinstance(s["id"], (str, int)) or isinstance(s["id"], bool):
            raise ValueError("invalid scenario id")
        if not str(s["id"]).strip() or str(s["id"]) in seen:
            raise ValueError("scenario ids must be unique and nonempty")
        seen.add(str(s["id"]))
        if not isinstance(s["item"], str) or not s["item"].strip():
            raise ValueError("item must be a nonempty string")
        if any(type(s[k]) is not int or s[k] < 0 for k in ("reserve", "budget")):
            raise ValueError("limits must be nonnegative integers")
    if {s["reserve"] <= s["budget"] for s in value} != {True, False}:
        raise ValueError("both possible and impossible scenarios required")
    return value


def row_key(row: dict) -> tuple[str, str, str]:
    return str(row["condition"]), str(row["run"]), str(row["scenario"])


def read_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HEADER:
            raise ValueError("results header differs from the required contract")
        rows = list(reader)
    keys = set()
    for row in rows:
        if None in row or any(v is None for v in row.values()):
            raise ValueError("incomplete CSV row; preserve and investigate it")
        key = row_key(row)
        if key in keys:
            raise ValueError("duplicate episode key")
        keys.add(key)
    return rows


def write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, HEADER, lineterminator="\n")
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in HEADER})
        handle.flush()
        os.fsync(handle.fileno())


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A killed process can leave a partial final line. Do not rewrite it.
            # Ignore only non-JSON fragments; later valid recovery events survive.
            if index != len(lines) - 1 and not any(
                '"event": "log_recovery"' in later for later in lines[index + 1:]
            ):
                raise ValueError(f"invalid log line {index + 1} in {path.name}")
    return events


class JsonlLogger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = False
        if path.exists() and path.stat().st_size:
            with path.open("rb") as source:
                source.seek(-1, os.SEEK_END)
                partial = source.read(1) != b"\n"
        self.handle = path.open("a", encoding="utf-8")
        self.context: dict = {}
        if partial:
            self.handle.write("\n")
            self({"event": "log_recovery", "detail": "preserved incomplete final line"})

    def __call__(self, event: dict) -> None:
        event = {"logged_at": datetime.now(timezone.utc).isoformat(), **self.context, **event}
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        self.handle.write(line + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        self.handle.close()


@contextmanager
def single_writer(output_dir: Path):
    with (output_dir / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another runner owns this output directory") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def experiment_config(backend, scenarios: list[dict], max_turns: int, config: dict | None) -> dict:
    backend_config = backend.configuration() if hasattr(backend, "configuration") else {
        "backend": type(backend).__name__
    }
    return {
        "backend": backend_config, "extra": config or {}, "max_turns": max_turns,
        "scenarios": scenarios,
        "prompts": {"ROLE": ROLE, "COMMON": COMMON, "FORMAT": FORMAT,
                    "READER_SYSTEM": READER_SYSTEM, "OPENING_CUE": OPENING_CUE},
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("model_client.py", "prompts.py", "protocol.py",
                         "negotiation.py", "run_experiment.py")
        },
        "protocol_policy": "nonnegative_integer;structured_leading_object;reader_strict_json",
    }


def ensure_manifest(output_dir: Path, payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    path = output_dir / "experiment.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["fingerprint"] != digest:
            raise ValueError("experiment fingerprint mismatch; use a separate output directory")
    else:
        if (output_dir / "results.csv").exists() or any((output_dir / "logs").glob("*.log")):
            raise ValueError("existing records without experiment manifest")
        try:
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            revision = "unavailable"
        try:
            sdk = importlib.metadata.version("openai")
        except importlib.metadata.PackageNotFoundError:
            sdk = "not installed"
        with path.open("x", encoding="utf-8") as handle:
            json.dump({
                "fingerprint": digest, "configuration": payload,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "git_commit": revision, "python": platform.python_version(),
                "openai": sdk,
            }, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return digest


def make_row(run: int, condition: str, scenario: dict, result: dict) -> dict:
    row = {
        "run": run, "condition": condition, "scenario": scenario["id"],
        "deal_possible": int(scenario["reserve"] <= scenario["budget"]),
        **{k: result.get(k, "") for k in
           ("outcome", "price", "correct", "violation", "turns", "format_errors", "reader_calls")},
    }
    row["note"] = result.get("note", "")
    return row


def run_batch(backend, scenarios: list[dict], output_dir: Path, *,
              repeats: int = 3, max_turns: int = 8,
              conditions=CONDITIONS, config: dict | None = None) -> list[dict]:
    if repeats < 1 or max_turns < 1 or any(c not in CONDITIONS for c in conditions):
        raise ValueError("invalid experiment settings")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    appended = []
    with single_writer(output_dir):
        fingerprint = ensure_manifest(
            output_dir, experiment_config(backend, scenarios, max_turns, config)
        )
        csv_path = output_dir / "results.csv"
        existing = read_results(csv_path)
        seen = {row_key(row) for row in existing}
        for run in range(1, repeats + 1):
            for condition in conditions:
                log_path = output_dir / "logs" / f"{condition}-run-{run:02d}.log"
                prior = read_events(log_path)
                if not prior and any(r["condition"] == condition and str(r["run"]) == str(run) for r in existing):
                    raise ValueError("CSV records exist without their run log")
                logger = JsonlLogger(log_path)
                logger.context = {"condition": condition, "run": run}
                if not prior:
                    logger({"event": "run_config", "fingerprint": fingerprint,
                            "max_turns": max_turns,
                            "model_config": experiment_config(backend, scenarios, max_turns, config)["backend"]})
                else:
                    logger({"event": "run_resume", "fingerprint": fingerprint})
                previous_retry = getattr(backend, "on_retry", None)
                if hasattr(backend, "on_retry"):
                    backend.on_retry = logger
                try:
                    for scenario in scenarios:
                        key = (condition, str(run), str(scenario["id"]))
                        logger.context["scenario"] = scenario["id"]
                        if key in seen:
                            continue
                        records = [e for e in prior if str(e.get("scenario")) == str(scenario["id"])]
                        finished = next((e["row"] for e in reversed(records) if e.get("event") == "episode_record"), None)
                        completed = next((e for e in reversed(records) if e.get("event") == "episode_result"), None)
                        started = any(e.get("event") == "episode_begin" for e in records)
                        if finished is not None:
                            row = finished
                            result = {}
                            logger({"event": "csv_recovered", "row": row})
                        elif completed is not None:
                            result = completed
                            row = make_row(run, condition, scenario, completed)
                            logger({"event": "episode_record", "row": row, "recovered_result": True})
                        elif started:
                            result = {"note": "crash=interrupted_before_result;partial_log_preserved"}
                            row = make_row(run, condition, scenario, result)
                            logger({"event": "episode_record", "row": row, "recovered_crash": True})
                        else:
                            logger({"event": "episode_begin", "scenario_config": scenario})
                            result = run_episode(
                                scenario, condition, backend, max_turns=max_turns, log=logger
                            )
                            row = make_row(run, condition, scenario, result)
                            logger({"event": "episode_record", "row": row})
                        write_row(csv_path, row)
                        appended.append(row)
                        seen.add(key)
                        if result.get("interrupted") or result.get("status_code") in {400, 401, 403, 404, 429}:
                            logger({"event": "batch_stopped", "reason": "interrupt_or_provider_setup_or_quota"})
                            return appended
                    logger({"event": "run_complete"})
                finally:
                    if hasattr(backend, "on_retry"):
                        backend.on_retry = previous_retry
                    logger.close()
    return appended


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "scenarios.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--condition", choices=CONDITIONS, action="append")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--reasoning-effort", default="none")
    args = parser.parse_args()
    scenarios = load_scenarios(args.scenarios)
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY is missing; set it in the environment")
    backend = OpenAIBackend(args.model, args.temperature, args.reasoning_effort)
    try:
        run_batch(backend, scenarios, args.output_dir, repeats=args.runs,
                  max_turns=args.max_turns, conditions=args.condition or CONDITIONS)
    finally:
        backend.close()
    rows = read_results(args.output_dir / "results.csv")
    expected = {(c, str(r), str(s["id"])) for c in args.condition or CONDITIONS
                for r in range(1, args.runs + 1) for s in scenarios}
    return 0 if expected <= {row_key(row) for row in rows} else 1


if __name__ == "__main__":
    raise SystemExit(main())
