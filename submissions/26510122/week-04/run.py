#!/usr/bin/env python3
"""Run buyer/seller negotiations under free, tagged, and structured formats."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable
import urllib.error
import urllib.request

from eristic import eristic_role_prompt
from protocol import (
    READER_PROMPT,
    ParsedMessage,
    parse_free,
    parse_structured,
    parse_tagged,
    role_prompt,
    verdict,
)


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
HEADER = [
    "run", "condition", "scenario", "deal_possible", "outcome", "price",
    "correct", "violation", "turns", "format_errors", "reader_calls", "note",
]
CONDITIONS = ("free", "tagged", "structured")


class ProviderError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def load_env() -> None:
    """Load simple KEY=VALUE entries without adding a package dependency."""
    for candidate in (REPO_ROOT / ".env", ROOT / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Chat:
    def __init__(self, model: str, temperature: float) -> None:
        self.backend_name = "openrouter"
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
        self.timeout = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "180"))
        self.max_retries = int(os.environ.get("AGENT_MAX_RETRIES", "2"))
        self.calls = 0
        self.retries = 0

    def __call__(self, system: str, user: str, json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": 180,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if self.base_url == "https://openrouter.ai/api/v1":
            payload["reasoning"] = {"enabled": False}
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(self.max_retries + 1):
            self.calls += 1
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.load(response)
                content = _completion_content(body)
                return content
            except urllib.error.HTTPError as exc:
                code = exc.code
                error = ProviderError(f"HTTP {code} from model provider", code)
            except ProviderError as exc:
                code = exc.code
                error = exc
            if code not in (429, 502, 503, 504) or attempt >= self.max_retries:
                raise error
            self.retries += 1
            time.sleep(min(2 ** attempt, 4))
        raise AssertionError("retry loop exhausted")


class ClaudeCLIChat:
    """Stateless model calls through the course-approved Claude Code CLI path."""

    def __init__(self, model: str, temperature: float | None) -> None:
        if temperature is not None:
            raise ValueError("claude-cli does not expose temperature")
        self.backend_name = "claude-cli"
        self.model = model
        self.temperature = "not-settable"
        self.calls = 0
        self.retries = 0
        self.timeout = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "180"))

    def __call__(self, system: str, user: str, json_mode: bool = False) -> str:
        del json_mode  # Output constraints remain in the fixed prompts.
        self.calls += 1
        command = [
            "claude", "-p", user,
            "--model", self.model,
            "--system-prompt", system,
            "--output-format", "text",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--tools", "",
        ]
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("claude-cli timed out") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip().splitlines()
            message = detail[-1] if detail else f"exit {completed.returncode}"
            raise ProviderError(f"claude-cli failed: {message}")
        if not completed.stdout.strip():
            raise ProviderError("claude-cli returned no completion")
        return completed.stdout.strip()


class CodexCLIChat:
    """Use a fixed developer message through a stateless Codex CLI call."""

    def __init__(self, model: str, temperature: float | None) -> None:
        if temperature is not None:
            raise ValueError("codex-cli does not expose temperature")
        self.backend_name = "codex-cli"
        self.model = model
        self.temperature = "not-settable"
        self.calls = 0
        self.retries = 0
        self.timeout = float(os.environ.get("AGENT_TIMEOUT_SECONDS", "180"))

    def __call__(self, system: str, user: str, json_mode: bool = False) -> str:
        del json_mode  # Output constraints remain in the fixed prompts.
        self.calls += 1
        developer = system + "\n\nDo not use tools or inspect files. Produce only the requested message."
        with tempfile.NamedTemporaryFile(prefix="week04-codex-", delete=False) as tmp:
            output_path = Path(tmp.name)
        command = [
            "codex", "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--cd", "/tmp",
            "--model", self.model,
            "--config", f"developer_instructions={json.dumps(developer)}",
            "--output-last-message", str(output_path),
            user,
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()
                message = detail[-1] if detail else f"exit {completed.returncode}"
                raise ProviderError(f"codex-cli failed: {message}")
            content = output_path.read_text(encoding="utf-8").strip()
            if not content:
                raise ProviderError("codex-cli returned no completion")
            return content
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("codex-cli timed out") from exc
        finally:
            output_path.unlink(missing_ok=True)


def _completion_content(body: dict) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError
        return content.strip()
    except (KeyError, IndexError, TypeError, ValueError):
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise ProviderError(message or "provider returned no completion", code)


def _agent_input(scenario: dict, speaker: str, turn: int, transcript: list[dict]) -> str:
    return json.dumps({
        "item": scenario["item"],
        "turn": turn,
        "you_are": speaker,
        "conversation": transcript,
        "instruction": "Send your next negotiation message.",
    }, ensure_ascii=False)


def run_episode(
    scenario: dict,
    condition: str,
    turn_limit: int,
    agent_chat: Callable[[str, str, bool], str],
    reader_chat: Callable[[str], str],
    emit: Callable[..., None],
    seller_strategy: str = "baseline",
):
    transcript: list[dict] = []
    last_proposal: tuple[str, int] | None = None
    format_errors = 0
    reader_calls = 0
    outcome = "open"
    deal_price = None

    def read_with_model(message: str) -> str:
        nonlocal reader_calls
        reader_calls += 1
        return reader_chat(message)

    for turn in range(1, turn_limit + 1):
        speaker = "buyer" if turn % 2 else "seller"
        prompt_builder = eristic_role_prompt if seller_strategy == "eristic" else role_prompt
        system = prompt_builder(speaker, scenario, condition)
        raw = agent_chat(
            system,
            _agent_input(scenario, speaker, turn, transcript),
            condition == "structured",
        )
        transcript.append({"speaker": speaker, "message": raw})
        emit("message", scenario=scenario["id"], turn=turn, speaker=speaker, raw=raw)

        if condition == "free":
            parsed = parse_free(raw, read_with_model)
        elif condition == "tagged":
            parsed = parse_tagged(raw, read_with_model)
        else:
            parsed = parse_structured(raw)

        error = parsed.error
        if not error and parsed.performative in ("accept-proposal", "reject-proposal"):
            if last_proposal is None or last_proposal[0] == speaker:
                error = f"{parsed.performative} has no proposal from the other agent"
                parsed = ParsedMessage(None, None, error)
        emit(
            "read", scenario=scenario["id"], turn=turn,
            performative=parsed.performative, price=parsed.price, error=error,
            reader_calls=reader_calls,
        )
        if error:
            format_errors += 1
            continue
        if parsed.performative == "propose":
            last_proposal = (speaker, parsed.price)
        elif parsed.performative == "accept-proposal":
            outcome = "deal"
            deal_price = last_proposal[1]
            break
        elif parsed.performative == "refuse":
            outcome = "no_deal"
            break

    result = verdict(
        scenario, outcome, deal_price, len(transcript), format_errors, reader_calls
    )
    emit("episode_result", scenario=scenario["id"], **asdict(result))
    return result


def append_row(path: Path, row: dict) -> None:
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=HEADER, lineterminator="\n")
        if new:
            writer.writeheader()
        writer.writerow(row)


def recorded_pairs(path: Path) -> set[tuple[str, str]]:
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as stream:
        return {(row["run"], row["scenario"]) for row in csv.DictReader(stream)}


def run_one(
    run_id: str,
    condition: str,
    scenarios: list[dict],
    turn_limit: int,
    client,
    results_path: Path | None,
    log_path: Path,
    seller_strategy: str,
) -> bool:
    done = recorded_pairs(results_path) if results_path else set()
    pending = [s for s in scenarios if (run_id, str(s["id"])) not in done]
    if not pending:
        print(json.dumps({"event": "skip_run", "run": run_id}, ensure_ascii=False))
        return True

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        def emit(event: str, **data) -> None:
            line = json.dumps({"event": event, "run": run_id, **data}, ensure_ascii=False)
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        emit(
            "setup", condition=condition, backend=client.backend_name, model=client.model,
            temperature=client.temperature, turn_limit=turn_limit,
            reader_prompt=READER_PROMPT, seller_strategy=seller_strategy,
            pending=[s["id"] for s in pending],
        )
        for scenario in pending:
            before_calls, before_retries = client.calls, client.retries
            try:
                result = run_episode(
                    scenario,
                    condition,
                    turn_limit,
                    client,
                    lambda raw: client(READER_PROMPT, raw, True),
                    emit,
                    seller_strategy,
                )
                row = {
                    "run": run_id,
                    "condition": condition,
                    "scenario": scenario["id"],
                    "deal_possible": int(scenario["reserve"] <= scenario["budget"]),
                    **asdict(result),
                    "note": "",
                }
                append_row(results_path, row) if results_path else None
                emit(
                    "usage", scenario=scenario["id"],
                    model_calls=client.calls - before_calls,
                    provider_retries=client.retries - before_retries,
                )
            except Exception as exc:
                note = type(exc).__name__
                if isinstance(exc, ProviderError) and exc.code is not None:
                    note += f":{exc.code}"
                row = dict.fromkeys(HEADER, "")
                row.update(
                    run=run_id, condition=condition, scenario=scenario["id"],
                    deal_possible=int(scenario["reserve"] <= scenario["budget"]),
                    note=note,
                )
                append_row(results_path, row) if results_path else None
                emit(
                    "crash", scenario=scenario["id"], note=note,
                    model_calls=client.calls - before_calls,
                    provider_retries=client.retries - before_retries,
                )
                return False
        emit("run_complete", condition=condition, scenarios=len(pending))
    return True


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--condition", choices=CONDITIONS)
    group.add_argument("--all", action="store_true")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--turn-limit", type=int, default=6)
    parser.add_argument("--run-prefix", default="")
    parser.add_argument(
        "--seller-strategy", choices=("baseline", "eristic"), default="baseline"
    )
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--backend", choices=("openrouter", "claude-cli", "codex-cli"),
        default=os.environ.get("AGENT_BACKEND", "openrouter"),
    )
    parser.add_argument("--model", default=os.environ.get("AGENT_MODEL"))
    parser.add_argument("--temperature", type=float)
    args = parser.parse_args()
    if args.runs < 1 or args.turn_limit < 1:
        parser.error("--runs and --turn-limit must be positive")
    if args.limit is not None and (not args.smoke or args.limit < 1):
        parser.error("--limit must be positive and used with --smoke")

    scenarios = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))
    if args.limit is not None:
        scenarios = scenarios[:args.limit]
    conditions = CONDITIONS if args.all else (args.condition,)
    if args.backend == "openrouter":
        model = args.model or "nvidia/nemotron-3-super-120b-a12b:free"
        temperature = 0.0 if args.temperature is None else args.temperature
        client = Chat(model, temperature)
    elif args.backend == "claude-cli":
        client = ClaudeCLIChat(args.model or "haiku", args.temperature)
    else:
        client = CodexCLIChat(args.model or "gpt-5.6-sol", args.temperature)
    if args.smoke:
        results_path = None
        log_dir = ROOT / "smoke"
    elif args.seller_strategy == "eristic":
        results_path = ROOT / "eristic_results.csv"
        log_dir = ROOT / "eristic_logs"
    else:
        results_path = ROOT / "results.csv"
        log_dir = ROOT / "logs"

    run_prefix = args.run_prefix
    if args.seller_strategy == "eristic" and not run_prefix:
        run_prefix = "eristic-"

    for condition in conditions:
        for repeat in range(1, args.runs + 1):
            suffix = "smoke" if args.smoke else f"r{repeat:02d}"
            run_id = f"{run_prefix}{condition}-{suffix}"
            ok = run_one(
                run_id, condition, scenarios, args.turn_limit, client,
                results_path, log_dir / f"{run_id}.jsonl",
                args.seller_strategy,
            )
            if not ok:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
