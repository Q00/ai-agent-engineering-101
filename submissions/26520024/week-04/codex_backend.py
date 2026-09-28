"""Tool-free model transport, adapted from the student's week-03 backend."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

MODEL = "gpt-6-astra"
REASONING = "low"
TIMEOUT = 180
RETRY_DELAYS = (2, 4, 8)
DISABLED = ("shell_tool", "unified_exec", "multi_agent", "apps", "plugins", "hooks",
            "browser_use", "computer_use", "image_generation", "view_image", "sleep_tool")
INSTRUCTION = (
    "Act as the language model for the enclosed system instruction and message "
    "history. Continue that conversation with exactly one final assistant reply. "
    "Do not use native Codex tools, inspect files, or execute code. Return only "
    "the reply required by the enclosed system instruction, without a wrapper."
)


class RateLimited(RuntimeError):
    pass


def is_rate_limit(text):
    return bool(re.search(r"(?:HTTP\s*429|status(?: code)?[: =]*429|rate_limit_exceeded|too_many_requests)",
                          text, re.IGNORECASE))


def executable():
    binary = shutil.which(os.environ.get("CODEX_BIN", "codex"))
    if binary is None:
        raise RuntimeError("Codex CLI not found; set CODEX_BIN")
    return binary


def backend_info():
    version = subprocess.run([executable(), "--version"], capture_output=True,
                             text=True, timeout=10, check=True)
    status = subprocess.run([executable(), "login", "status"], capture_output=True,
                            text=True, timeout=10)
    if status.returncode:
        raise RuntimeError("Authenticate Codex in your terminal first")
    return dict(provider="OpenAI via Codex CLI", model=MODEL, reasoning_effort=REASONING,
                cli_version=version.stdout.strip(), authentication="ChatGPT" if
                "ChatGPT" in status.stdout + status.stderr else "configured",
                temperature="not directly configurable; internal value unknown",
                max_output_tokens="not directly configurable; internal value unknown",
                timeout_seconds=TIMEOUT, retry_delays=list(RETRY_DELAYS), response_schema=None)


def payload(system, history):
    return dict(instructions=INSTRUCTION, system=system,
                history=[dict(message) for message in history])


def inspect_events(stdout):
    usages, texts = [], []
    for line in stdout.splitlines():
        event = json.loads(line)
        if event.get("type") in ("error", "turn.failed"):
            if is_rate_limit(line):
                raise RateLimited("rate-limited model turn; see original events")
            raise RuntimeError("failed model turn; see original events")
        item = event.get("item", {})
        if item and item.get("type") not in ("agent_message", "reasoning"):
            raise RuntimeError("native tool action detected")
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            texts.append(item.get("text"))
        if event.get("type") == "turn.completed":
            usages.append(event.get("usage", {}))
    if len(usages) != 1 or not texts or not isinstance(texts[-1], str):
        raise RuntimeError("expected one completed model turn with final text")
    for key in ("input_tokens", "output_tokens"):
        if type(usages[0].get(key)) is not int or usages[0][key] < 0:
            raise RuntimeError("missing or invalid usage")
    return texts[-1], usages[0]


def invoke(system, history, emit):
    request = payload(system, history)
    emit("model_input", payload=request)
    with tempfile.TemporaryDirectory(prefix="week04-codex-") as directory:
        output = Path(directory) / "reply.txt"
        argv = [executable(), "exec", "--ignore-user-config", "--ephemeral",
                "--sandbox", "read-only", "--skip-git-repo-check", "--cd", directory,
                "--model", MODEL, "--output-last-message", str(output), "--json",
                "--color", "never", "-c", 'web_search="disabled"',
                "-c", 'model_reasoning_effort="{}"'.format(REASONING)]
        for feature in DISABLED:
            argv.extend(["--disable", feature])
        argv.append("-")
        emit("model_command", argv=argv)
        try:
            result = subprocess.run(argv, input=json.dumps(request), text=True,
                                    capture_output=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            for stream, raw in (("stdout", exc.stdout), ("stderr", exc.stderr)):
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                emit("model_timeout", stream=stream, raw=raw or "")
            raise
        emit("model_output", stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
        if result.returncode:
            if is_rate_limit(result.stdout + result.stderr):
                raise RateLimited("HTTP 429; see original output")
            raise RuntimeError("Codex exit status {}".format(result.returncode))
        event_text, usage = inspect_events(result.stdout)
        raw = output.read_text(encoding="utf-8")
        emit("model_response", raw=raw, usage=usage)
        if raw.strip() != event_text.strip():
            raise RuntimeError("final text differs from raw event")
        return raw, usage


class ModelSession:
    def __init__(self, emit, transport=invoke, sleep=time.sleep):
        self.emit, self.transport, self.sleep = emit, transport, sleep
        self.stats = dict(actor_calls=0, reader_calls=0, retries=0, input_tokens=0, output_tokens=0)

    def __call__(self, system, history, purpose):
        if purpose not in ("buyer", "seller", "reader"):
            raise ValueError("unknown call purpose")
        for attempt in range(len(RETRY_DELAYS) + 1):
            key = "reader_calls" if purpose == "reader" else "actor_calls"
            self.stats[key] += 1
            call = self.stats["actor_calls"] + self.stats["reader_calls"]
            def emit(event, **data):
                self.emit(event, call=call, purpose=purpose, **data)
            emit("call_start", attempt=attempt + 1)
            try:
                raw, usage = self.transport(system, history, emit)
            except (Exception, KeyboardInterrupt) as exc:
                emit("call_error", kind=type(exc).__name__, error=str(exc))
                if isinstance(exc, RateLimited) and attempt < len(RETRY_DELAYS):
                    delay = RETRY_DELAYS[attempt]
                    self.stats["retries"] += 1
                    emit("retry", delay_seconds=delay)
                    self.sleep(delay)
                    continue
                raise
            for key in ("input_tokens", "output_tokens"):
                self.stats[key] += usage[key]
            emit("call_end", usage=usage)
            return raw
