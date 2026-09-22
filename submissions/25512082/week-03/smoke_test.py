"""Three-call JSON-contract smoke test, kept separate from formal results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from contract_net import (
    ANNOUNCEMENT,
    BASE_URL,
    MAX_TOKENS,
    MODEL,
    PROVIDER,
    REASONING_ENABLED,
    TEMPERATURE,
    Meter,
    OpenAICompatibleChat,
    make_team,
    parse_bid,
    protocol_fingerprint,
    validate_runtime_config,
)
from run_experiment import (
    api_error_metadata,
    is_rate_limit_error,
    load_tasks,
    safe_text,
    smoke_ready_path,
)


SMOKE_DIAGNOSTIC_VERSION = "metadata-v2"


def next_smoke_log_path(log_dir: Path) -> Path:
    index = 1
    while True:
        path = log_dir / f"smoke-baseline-task01-{index:02d}.txt"
        if not path.exists():
            return path
        index += 1


def main() -> None:
    validate_runtime_config()

    base = Path(__file__).resolve().parent
    tasks = load_tasks(base / "tasks.json")
    task = tasks[0]
    fingerprint = protocol_fingerprint(tasks)
    smoke_dir = base / "smoke_logs"
    smoke_dir.mkdir(exist_ok=True)
    attempt_path = (
        smoke_dir
        / f"smoke-attempt-{fingerprint}-{SMOKE_DIAGNOSTIC_VERSION}.json"
    )
    try:
        with attempt_path.open("x", encoding="utf-8") as attempt_file:
            json.dump(
                {
                    "protocol_fingerprint": fingerprint,
                    "diagnostic_version": SMOKE_DIAGNOSTIC_VERSION,
                    "maximum_calls": 3,
                },
                attempt_file,
                indent=2,
            )
            attempt_file.write("\n")
    except FileExistsError as exc:
        raise SystemExit(
            "this metadata smoke test was already attempted; refusing another API run"
        ) from exc
    log_path = next_smoke_log_path(smoke_dir)
    lines: list[str] = []

    def log(message: object) -> None:
        clean = safe_text(message)
        print(clean)
        lines.extend(clean.splitlines() or [""])

    log(f"[setup] provider={PROVIDER}")
    log(f"[setup] model={MODEL}")
    log(f"[setup] base_url={BASE_URL}")
    log(f"[setup] temperature={TEMPERATURE:g}")
    log(f"[setup] max_tokens={MAX_TOKENS}")
    log(f"[setup] reasoning_enabled={str(REASONING_ENABLED).lower()}")
    log("[setup] diagnostic_only=true")
    log(f"[setup] protocol_fingerprint={fingerprint}")

    announcement = ANNOUNCEMENT.format(task_id=task["id"], desc=task["desc"])
    caller = OpenAICompatibleChat(Meter())
    successes = 0
    completed_calls = 0
    api_errors = 0
    try:
        for contractor in make_team("baseline"):
            log(f"[announcement] task_id={task['id']} contractor={contractor.name}")
            log(announcement)
            raw = caller(contractor.system_prompt, announcement)
            completed_calls += 1
            log(
                f"[response_metadata] contractor={contractor.name} "
                f"{json.dumps(caller.last_metadata, ensure_ascii=False, sort_keys=True)}"
            )
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
        api_errors += 1
        log(
            "[api_error_metadata] "
            + json.dumps(api_error_metadata(exc), ensure_ascii=False, sort_keys=True)
        )
        log(f"[crash] {type(exc).__name__}: {safe_text(exc)}")
        if is_rate_limit_error(exc):
            log("[stop] rate limit reached; no further smoke calls were attempted")
    finally:
        with log_path.open("x", encoding="utf-8") as log_file:
            log_file.write("\n".join(lines) + "\n")

    if completed_calls != 3 or api_errors or successes != 3:
        raise SystemExit(
            f"smoke test blocked: completed_calls={completed_calls}/3; "
            f"api_errors={api_errors}; strict_json={successes}/3; "
            f"diagnostic log: {log_path}"
        )

    ready_path = smoke_ready_path(base, tasks)
    if not ready_path.exists():
        record = {
            "protocol_fingerprint": fingerprint,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "calls_completed": completed_calls,
            "api_errors": api_errors,
            "strict_json_successes": successes,
            "log": log_path.name,
        }
        with ready_path.open("x", encoding="utf-8") as ready_file:
            json.dump(record, ready_file, ensure_ascii=False, indent=2)
            ready_file.write("\n")
    print(
        f"smoke test completed: calls=3/3; api_errors=0; "
        f"strict_json=3/3; gate file: {ready_path}"
    )


if __name__ == "__main__":
    main()
