"""Three-call JSON-contract smoke test, kept separate from formal results."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from contract_net import (
    ANNOUNCEMENT,
    MAX_TOKENS,
    MODEL,
    PROVIDER,
    REASONING_ENABLED,
    TEMPERATURE,
    Meter,
    OpenRouterChat,
    make_team,
    parse_bid,
    protocol_fingerprint,
)
from run_experiment import is_rate_limit_error, load_tasks, safe_text, smoke_pass_path


def next_smoke_log_path(log_dir: Path) -> Path:
    index = 1
    while True:
        path = log_dir / f"smoke-baseline-task01-{index:02d}.txt"
        if not path.exists():
            return path
        index += 1


def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; smoke test was not started")

    base = Path(__file__).resolve().parent
    tasks = load_tasks(base / "tasks.json")
    task = tasks[0]
    fingerprint = protocol_fingerprint(tasks)
    smoke_dir = base / "smoke_logs"
    smoke_dir.mkdir(exist_ok=True)
    log_path = next_smoke_log_path(smoke_dir)
    lines: list[str] = []

    def log(message: object) -> None:
        clean = safe_text(message)
        print(clean)
        lines.extend(clean.splitlines() or [""])

    log(f"[setup] provider={PROVIDER}")
    log(f"[setup] model={MODEL}")
    log(f"[setup] temperature={TEMPERATURE:g}")
    log(f"[setup] max_tokens={MAX_TOKENS}")
    log(f"[setup] reasoning_enabled={str(REASONING_ENABLED).lower()}")
    log("[setup] diagnostic_only=true")
    log(f"[setup] protocol_fingerprint={fingerprint}")

    announcement = ANNOUNCEMENT.format(task_id=task["id"], desc=task["desc"])
    caller = OpenRouterChat(Meter())
    successes = 0
    try:
        for contractor in make_team("baseline"):
            log(f"[announcement] task_id={task['id']} contractor={contractor.name}")
            log(announcement)
            raw = caller(contractor.system_prompt, announcement)
            log(f"[raw_response_begin] contractor={contractor.name}")
            log(raw)
            log(f"[raw_response_end] contractor={contractor.name}")
            try:
                bid = parse_bid(raw)
            except ValueError as exc:
                log(f"[parse_failure] contractor={contractor.name} error={exc}")
                continue
            successes += 1
            log(
                f"[parse_success] contractor={contractor.name} "
                f"bid={str(bid.bid).lower()} confidence={bid.confidence:g} "
                f"reason={json.dumps(bid.reason, ensure_ascii=False)}"
            )
    except Exception as exc:
        log(f"[crash] {type(exc).__name__}: {safe_text(exc)}")
        if is_rate_limit_error(exc):
            log("[stop] rate limit reached; no further smoke calls were attempted")
    finally:
        with log_path.open("x", encoding="utf-8") as log_file:
            log_file.write("\n".join(lines) + "\n")

    if successes != 3:
        raise SystemExit(
            f"smoke test failed: {successes}/3 strict JSON responses; "
            f"diagnostic log: {log_path}"
        )

    pass_path = smoke_pass_path(base, tasks)
    if not pass_path.exists():
        record = {
            "protocol_fingerprint": fingerprint,
            "passed_at_utc": datetime.now(timezone.utc).isoformat(),
            "calls": 3,
            "log": log_path.name,
        }
        with pass_path.open("x", encoding="utf-8") as pass_file:
            json.dump(record, pass_file, ensure_ascii=False, indent=2)
            pass_file.write("\n")
    print(f"smoke test passed: 3/3 strict JSON responses; pass file: {pass_path}")


if __name__ == "__main__":
    main()
