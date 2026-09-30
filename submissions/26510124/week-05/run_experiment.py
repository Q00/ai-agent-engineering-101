#!/usr/bin/env python3
"""Run the fixed MCP market experiment, preserving completed and crashed episodes."""

from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parent
CONDITIONS = ("prompt", "server", "prompt_inject", "server_inject")
SOURCES = ("market.py", "market_server.py", "host.py", "prompts.py", "run_experiment.py",
           "requirements.txt", "requirements-lock.txt")
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "attempted_violations", "refused_calls", "turns",
    "tool_calls", "note",
]


def load_scenarios(path: Path) -> list[dict]:
    scenarios = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(scenarios, list) or len(scenarios) < 4:
        raise ValueError("at least four scenarios required")
    seen = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict) or set(scenario) != {"id", "item", "reserve", "budget"}:
            raise ValueError("scenario fields must be id, item, reserve, budget")
        if not isinstance(scenario["id"], str) or not scenario["id"].strip():
            raise ValueError("scenario ids must be nonempty strings")
        if scenario["id"] in seen:
            raise ValueError("scenario ids must be unique")
        seen.add(scenario["id"])
        if not isinstance(scenario["item"], str) or not scenario["item"].strip():
            raise ValueError("scenario item must be a nonempty string")
        if any(type(scenario[key]) is not int or scenario[key] < 0 for key in ("reserve", "budget")):
            raise ValueError("limits must be nonnegative integers")
    if {scenario["reserve"] <= scenario["budget"] for scenario in scenarios} != {True, False}:
        raise ValueError("both possible and impossible scenarios required")
    return scenarios


def row_key(row: dict) -> tuple[str, str, str]:
    return str(row["run"]), str(row["condition"]), str(row["scenario"])


def read_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != HEADER:
            raise ValueError("results header differs from the required contract")
        rows = list(reader)
    seen = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("incomplete CSV row; preserve the file and investigate")
        key = row_key(row)
        if key in seen:
            raise ValueError(f"duplicate result key: {key}")
        seen.add(key)
    return rows


def write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, HEADER, lineterminator="\n")
        if handle.tell() == 0:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in HEADER})
        handle.flush()
        os.fsync(handle.fileno())


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    events = []
    for index, line in enumerate(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Only a torn final line (or one explicitly preserved on recovery)
            # is ignorable. Never rewrite an old console capture.
            recovered = False
            if index + 1 < len(lines):
                try:
                    recovered = json.loads(lines[index + 1]).get("event") == "log_recovery"
                except (json.JSONDecodeError, AttributeError):
                    pass
            if index != len(lines) - 1 and not recovered:
                raise ValueError(f"invalid log line {index + 1}: {path.name}") from None
            continue
        if not isinstance(event, dict):
            raise ValueError(f"non-object log event: {path.name}:{index + 1}")
        events.append(event)
    return events


def redact(value, secret_values: list[str]):
    if isinstance(value, str):
        for secret in secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", value)
    if isinstance(value, dict):
        return {key: redact(item, secret_values) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secret_values) for item in value]
    return value


class JsonlLogger:
    """Append-only console capture; each event is flushed before control returns."""

    def __init__(self, path: Path, secret_values: list[str] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.context: dict = {}
        self.secret_values = secret_values if secret_values is not None else []
        partial = False
        if path.exists() and path.stat().st_size:
            with path.open("rb") as source:
                source.seek(-1, os.SEEK_END)
                partial = source.read(1) != b"\n"
        self.handle = path.open("a", encoding="utf-8")
        if partial:
            self.handle.write("\n")
            self({"event": "log_recovery", "detail": "preserved incomplete final line"})

    def __call__(self, event: dict) -> None:
        event = redact({"logged_at": datetime.now(timezone.utc).isoformat(), **self.context, **event}, self.secret_values)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        self.handle.write(line + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())
        try:
            print(line, flush=True)
        except BrokenPipeError:
            # The durable capture is authoritative even if a console consumer exits.
            pass

    def close(self) -> None:
        self.handle.close()


@contextmanager
def single_writer(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / ".run.lock").open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another runner owns this output directory") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def assert_committed(paths: list[Path]) -> str:
    """Require experiment inputs to exist in HEAD before paying for model calls."""
    try:
        repository = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=ROOT, text=True).strip())
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        for path in paths:
            relative = path.resolve().relative_to(repository)
            committed = subprocess.check_output(["git", "show", f"HEAD:{relative.as_posix()}"], cwd=repository, stderr=subprocess.DEVNULL)
            if committed != path.read_bytes():
                raise ValueError(f"commit the experiment input before running: {relative}")
        return revision
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        raise ValueError(f"source and scenarios must be committed before the run: {error}") from None


def experiment_config(args, backend, scenarios: list[dict]) -> dict:
    return {
        "host": "week01-mcp-loop",
        "backend": backend.configuration(),
        "conditions": list(args.conditions),
        "runs": args.runs,
        "max_turns": args.max_turns,
        "max_model_rounds": args.max_model_rounds,
        "scenarios": scenarios,
        "order": "repeat outer; condition order rotated left by repeat-1; scenario file order",
        "turn_policy": "one host invocation; finish_turn always; no valid move passes turn",
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
    }


def ensure_manifest(output_dir: Path, config: dict, revision: str) -> str:
    encoded = json.dumps(config, ensure_ascii=False, sort_keys=True).encode()
    fingerprint = hashlib.sha256(encoded).hexdigest()
    path = output_dir / "experiment.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("fingerprint") != fingerprint:
            raise ValueError("experiment fingerprint mismatch; use a separate output directory")
    else:
        if (output_dir / "results.csv").exists() or any((output_dir / "logs").glob("*.jsonl")):
            raise ValueError("existing experiment records have no manifest")
        versions = {}
        for package in ("mcp", "openai", "httpx2"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = "unavailable"
        payload = {"fingerprint": fingerprint, "config": config, "source_commit": revision,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "python": platform.python_version(), "packages": versions}
        # Exclusive creation plus fsync avoids accidentally replacing provenance.
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    return fingerprint


def crash_row(run: int | str, condition: str, scenario: str, note: str, error: str) -> dict:
    row = dict.fromkeys(HEADER, "")
    row.update(run=run, condition=condition, scenario=scenario, note=f"{note};error={error}")
    return row


def recover_results(output_dir: Path, note: str) -> list[dict]:
    """Recover write-ahead results; retain interrupted episodes as crashed rows."""
    csv_path = output_dir / "results.csv"
    rows = read_results(csv_path)
    known = {row_key(row): row for row in rows}
    started: dict[tuple, tuple[Path, dict]] = {}
    records: dict[tuple, dict] = {}
    for path in sorted((output_dir / "logs").glob("*.jsonl")):
        for event in read_events(path):
            if event.get("event") == "episode_start":
                started[row_key(event)] = (path, event)
            if event.get("event") in ("episode_result", "episode_record") and "row" in event:
                row = event["row"]
                key = row_key(row)
                if key in records and records[key] != row:
                    raise ValueError(f"conflicting write-ahead records for {key}")
                records[key] = row
    for key, row in records.items():
        if key in known:
            normalized = {name: str(row.get(name, "")) for name in HEADER}
            if known[key] != normalized:
                raise ValueError(f"CSV and log disagree for {key}")
        else:
            write_row(csv_path, row)
            known[key] = {name: str(row.get(name, "")) for name in HEADER}
    for key, (path, event) in started.items():
        if key in known:
            continue
        error = "InterruptedEpisode: process stopped before result; previous negotiation discarded"
        row = crash_row(event["run"], event["condition"], event["scenario"], note, error)
        log = JsonlLogger(path)
        try:
            log.context = {name: event[name] for name in ("run", "condition", "scenario")}
            log({"event": "episode_crashed", "error": error})
            log({"event": "episode_record", "row": row})
            write_row(csv_path, row)
            known[key] = {name: str(row.get(name, "")) for name in HEADER}
        finally:
            log.close()
    return read_results(csv_path)


def condition_order(conditions: list[str], run: int) -> list[str]:
    offset = (run - 1) % len(conditions)
    return conditions[offset:] + conditions[:offset]


def ensure_scenarios_copy(output_dir: Path, source: Path, scenarios: list[dict]) -> None:
    """Keep a reproduced output directory independently readable by the analyzer."""
    target = output_dir / "scenarios.json"
    if target.exists():
        if load_scenarios(target) != scenarios:
            raise ValueError("output scenarios differ from the selected experiment input")
        return
    with target.open("xb") as handle:
        handle.write(source.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())


def fatal_configuration_error(error: BaseException) -> bool:
    """Stop after one recorded crash for a nonretryable provider/configuration error."""
    if isinstance(error, BaseExceptionGroup):
        return any(fatal_configuration_error(child) for child in error.exceptions)
    status = getattr(error, "status_code", None)
    return (type(error).__name__ == "ModelCallError" and isinstance(status, int)
            and 400 <= status < 500 and status not in (408, 409, 429))


def error_summary(error: BaseException) -> str:
    if isinstance(error, BaseExceptionGroup):
        return f"{type(error).__name__}: " + "; ".join(error_summary(child) for child in error.exceptions)
    return f"{type(error).__name__}: {error}"


def available_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@asynccontextmanager
async def managed_server(output_dir: Path, port: int, secret_values: list[str]):
    """Own a loopback market process; never pass model API credentials to it."""
    port = port or available_port()
    admin_token = secrets.token_urlsafe(32)
    secret_values.append(admin_token)
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT") if key in os.environ}
    environment.update(MARKET_ADMIN_TOKEN=admin_token, PYTHONUNBUFFERED="1")
    verification_dir = output_dir / "verification"
    verification_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    server_log_path = verification_dir / f"server-{stamp}-{os.getpid()}.txt"
    with server_log_path.open("x", encoding="utf-8") as server_log:
        process = subprocess.Popen([sys.executable, str(ROOT / "market_server.py"), "--port", str(port)],
                                   cwd=ROOT, env=environment, stdout=server_log, stderr=subprocess.STDOUT)
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                                         headers={"Authorization": f"Bearer {admin_token}"}, timeout=15, trust_env=False) as admin:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"market server exited with {process.returncode}; see {server_log_path.name}")
                    try:
                        response = await admin.get("/health")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("market server did not become healthy within 30 seconds")
                    await asyncio.sleep(0.1)
                yield f"http://127.0.0.1:{port}/mcp", admin
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait)


async def admin_json(admin, method: str, path: str, payload: dict | None = None) -> dict:
    response = await admin.request(method, path, json=payload)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("admin endpoint returned a non-object")
    return value


def result_row(run: int, condition: str, scenario: dict, snapshot: dict, note: str) -> dict:
    metrics = snapshot["metrics"]
    return {
        "run": run, "condition": condition, "scenario": scenario["id"],
        "deal_possible": int(scenario["reserve"] <= scenario["budget"]),
        "outcome": snapshot["status"], "price": snapshot["price"] if snapshot["price"] is not None else "",
        "correct": int(metrics["correct"]), "violation": int(metrics["violation"]),
        "attempted_violations": metrics["attempted_violations"], "refused_calls": metrics["refused_calls"],
        "turns": metrics["turns"], "tool_calls": metrics["tool_calls"],
        "note": f"{note};refusals_recovered={metrics['refusals_recovered']}",
    }


async def run_batch(args, *, backend_factory=None, turn_runner=None, server_factory=None,
                    require_committed: bool = True) -> list[dict]:
    """Run/resume with injectable dependencies for offline integration checks."""
    if backend_factory is None or turn_runner is None:
        from host import OpenAIBackend, run_turn
        backend_factory = backend_factory or OpenAIBackend
        turn_runner = turn_runner or run_turn
    server_factory = server_factory or managed_server
    scenarios = load_scenarios(Path(args.scenarios))
    output_dir = Path(args.output_dir).resolve()
    if args.runs < 1 or not 1 <= args.max_turns <= 8 or args.max_model_rounds < 1:
        raise ValueError("runs/model rounds must be positive and max turns must be 1..8")
    if not args.conditions or len(set(args.conditions)) != len(args.conditions) or any(c not in CONDITIONS for c in args.conditions):
        raise ValueError("conditions must be a nonempty unique selection of the four conditions")
    revision = assert_committed([ROOT / name for name in SOURCES] + [Path(args.scenarios)]) if require_committed else "offline-test"
    backend = backend_factory(model=args.model, temperature=args.temperature,
                              reasoning_effort=args.reasoning_effort,
                              max_completion_tokens=args.max_completion_tokens)
    note = f"host=week01-mcp-loop;model={args.model}"
    secret_values = [value for key, value in os.environ.items() if key.endswith(("API_KEY", "TOKEN")) and value]
    with single_writer(output_dir):
        ensure_scenarios_copy(output_dir, Path(args.scenarios), scenarios)
        fingerprint = ensure_manifest(output_dir, experiment_config(args, backend, scenarios), revision)
        rows = recover_results(output_dir, note)
        known = {row_key(row) for row in rows}
        keys = {(str(run), condition, scenario["id"]) for run in range(1, args.runs + 1)
                for condition in args.conditions for scenario in scenarios}
        if keys.issubset(known):
            # This also avoids requiring credentials just to inspect a completed run.
            return rows
        async with backend:
            async with server_factory(output_dir, args.port, secret_values) as (server_url, admin):
                for run in range(1, args.runs + 1):
                    for condition in condition_order(list(args.conditions), run):
                        pending = [scenario for scenario in scenarios if (str(run), condition, scenario["id"]) not in known]
                        if not pending:
                            continue
                        log = JsonlLogger(output_dir / "logs" / f"{condition}-run-{run:02d}.jsonl", secret_values)
                        try:
                            log.context = {"run": run, "condition": condition}
                            log({"event": "run_start", "fingerprint": fingerprint, "pending_scenarios": [s["id"] for s in pending]})
                            for scenario in pending:
                                context = {"run": run, "condition": condition, "scenario": scenario["id"]}
                                log.context = context
                                log({"event": "episode_start", "scenario_data": scenario, "max_turns": args.max_turns})
                                try:
                                    opened = await admin_json(admin, "POST", "/admin/negotiations",
                                                              {"scenario": scenario, "condition": condition, "max_turns": args.max_turns})
                                    negotiation_id = opened["negotiation_id"]
                                    tokens = {role: opened[f"{role}_token"] for role in ("buyer", "seller")}
                                    secret_values.extend(tokens.values())
                                    log({"event": "episode_open", "negotiation_id": negotiation_id})
                                    admin_path = f"/admin/negotiations/{negotiation_id}"
                                    snapshot = await admin_json(admin, "GET", admin_path)
                                    for turn_index in range(args.max_turns):
                                        if snapshot["status"] != "open":
                                            break
                                        role = snapshot["whose_turn"]
                                        log.context = {**context, "turn_index": turn_index, "role": role}
                                        log({"event": "host_turn_start", "negotiation_id": negotiation_id})
                                        try:
                                            result = await turn_runner(server_url=server_url, token=tokens[role], negotiation_id=negotiation_id,
                                                                       role=role, item=scenario["item"],
                                                                       limit=scenario["budget" if role == "buyer" else "reserve"],
                                                                       backend=backend, log=log, max_model_rounds=args.max_model_rounds)
                                            log({"event": "host_turn_end", "result": result})
                                        finally:
                                            # Even a host that returns without a valid move consumes its turn.
                                            snapshot = await admin_json(admin, "POST", f"{admin_path}/finish_turn", {"turn_index": turn_index})
                                            log({"event": "turn_finished", "snapshot": snapshot})
                                    log.context = context
                                    row = result_row(run, condition, scenario, snapshot, note)
                                    # Persist the complete result BEFORE the CSV, so restart can restore a missing row.
                                    log({"event": "episode_result", "snapshot": snapshot, "row": row})
                                except BaseException as error:
                                    log.context = context
                                    error_text = redact(error_summary(error), secret_values)
                                    row = crash_row(run, condition, scenario["id"], note, error_text)
                                    log({"event": "episode_crashed", "error": error_text})
                                    log({"event": "episode_record", "row": row})
                                    write_row(output_dir / "results.csv", row)
                                    known.add(row_key(row))
                                    if not isinstance(error, Exception) or fatal_configuration_error(error):
                                        raise
                                    continue
                                write_row(output_dir / "results.csv", row)
                                known.add(row_key(row))
                            log.context = {"run": run, "condition": condition}
                            log({"event": "run_complete"})
                        finally:
                            log.close()
    return read_results(output_dir / "results.csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT)
    parser.add_argument("--scenarios", type=Path, default=ROOT / "scenarios.json")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--conditions", nargs="+", choices=CONDITIONS, default=list(CONDITIONS))
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--reasoning-effort", default="none")
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--port", type=int, default=0, help="loopback port; 0 selects an available port")
    parser.add_argument("--max-model-rounds", type=int, default=8)
    parser.add_argument("--max-completion-tokens", type=int, default=500)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = asyncio.run(run_batch(args))
    completed = sum(row["outcome"] in ("deal", "no_deal", "open") for row in rows)
    print(json.dumps({"event": "batch_complete", "episodes": len(rows), "completed": completed,
                      "crashed": len(rows) - completed, "results": str(Path(args.output_dir).resolve() / "results.csv")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
