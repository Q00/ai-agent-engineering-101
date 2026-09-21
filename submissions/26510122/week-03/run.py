"""Run the required Contract Net experiment through an OpenAI-compatible API."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import uuid

from contract_net import CONDITIONS, run_contract_net


ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = ROOT.parents[2]
RESULT_HEADER = [
    "run", "condition", "tasks", "correct", "messages", "unassigned",
    "misawards", "note",
]


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without printing or overwriting secrets."""
    if not path.is_file():
        return
    bare_values = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            bare_values.append(line)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"").strip("'")
        if key:
            os.environ.setdefault(key, value)
    # The local course setup may contain only the OpenRouter token on one line.
    if len(bare_values) == 1:
        os.environ.setdefault("OPENAI_API_KEY", bare_values[0])


class OpenAICompatibleChat:
    def __init__(self, model: str, temperature: float) -> None:
        self.model = model
        self.temperature = temperature
        self.base_url = os.environ.get(
            "OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
        ).rstrip("/")
        self.api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get(
            "OPENROUTER_API_KEY"
        )
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY or OPENROUTER_API_KEY is missing")
        if not self.base_url.startswith("https://"):
            raise ValueError("OPENAI_BASE_URL must use HTTPS")
        self.timeout_seconds = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "180"))
        if self.timeout_seconds <= 0:
            raise ValueError("AGENT_TIMEOUT_SECONDS must be positive")
        self.calls = 0

    def __call__(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": 512,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.base_url == "https://openrouter.ai/api/v1":
            payload["reasoning"] = {"enabled": False}
            payload["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        self.calls += 1
        with urllib.request.urlopen(
            request, timeout=self.timeout_seconds
        ) as response:
            body = json.load(response)
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("provider returned no completion") from exc


def append_result(path: Path, row: dict) -> None:
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=RESULT_HEADER, lineterminator="\n"
        )
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def new_run_id(condition: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{condition}-{uuid.uuid4().hex[:6]}"


def execute_run(
    condition: str,
    model: str,
    temperature: float,
    smoke: bool,
    limit: int | None = None,
) -> str:
    run_id = new_run_id(condition)
    log_dir = ROOT / ("smoke" if smoke else "logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.jsonl"
    result_path = ROOT / "results.csv"

    with log_path.open("x", encoding="utf-8") as log:
        def emit(event: str, **data) -> None:
            record = {"event": event, **data}
            line = json.dumps(record, ensure_ascii=False)
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        client = None
        try:
            client = OpenAICompatibleChat(model, temperature)
            emit(
                "setup", run=run_id, condition=condition, model=model,
                temperature=temperature, endpoint=client.base_url,
                timeout_seconds=client.timeout_seconds,
            )
            tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))
            if limit is not None:
                tasks = tasks[:limit]
            metrics = run_contract_net(tasks, condition, client, emit)
            parse_fails = metrics.pop("parse_fails")
            row = {
                "run": run_id,
                "condition": condition,
                **metrics,
                "note": f"parse_fails={parse_fails}",
            }
            emit("summary", **row, parse_fails=parse_fails, llm_calls=client.calls)
            outcome = "ok"
        except Exception as exc:
            row = dict.fromkeys(RESULT_HEADER, "")
            row.update(run=run_id, condition=condition, note=type(exc).__name__)
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            emit("crash", **row, http_status=status, llm_calls=client.calls if client else 0)
            outcome = "rate_limited" if status == 429 else "failed"

    if not smoke:
        append_result(result_path, row)
    return outcome


def main() -> int:
    load_env(REPOSITORY_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--condition", choices=CONDITIONS)
    parser.add_argument("--all", action="store_true", help="run every condition")
    parser.add_argument("--runs", type=int, default=1, help="runs per condition")
    parser.add_argument("--smoke", action="store_true", help="do not write results.csv")
    parser.add_argument("--limit", type=int, help="limit tasks in smoke mode")
    parser.add_argument(
        "--model",
        default=os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free"),
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.limit is not None and (not args.smoke or args.limit < 1):
        parser.error("--limit must be positive and used with --smoke")
    if args.all == bool(args.condition):
        parser.error("choose exactly one of --condition or --all")

    conditions = CONDITIONS if args.all else (args.condition,)
    failures = 0
    for condition in conditions:
        for _ in range(args.runs):
            outcome = execute_run(
                condition, args.model, args.temperature, args.smoke, args.limit
            )
            if outcome == "rate_limited":
                print("OpenRouter rate limit reached; stopping remaining runs.")
                return 1
            failures += int(outcome != "ok")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
