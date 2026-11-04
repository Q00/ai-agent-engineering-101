"""Call the coding harness and preserve each attempt's raw output."""
import argparse
from datetime import datetime, timezone, timedelta
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


BASE = Path(__file__).resolve().parent
COOLDOWN_PATH = BASE / ".market" / "codex-cooldown.json"


class HarnessUsageLimit(RuntimeError):
    """A reported account limit, not a seller/buyer negotiation decision."""


def check_usage_cooldown():
    if COOLDOWN_PATH.exists():
        state = json.loads(COOLDOWN_PATH.read_text(encoding="utf-8"))
        if state["retry_after"] > time.time():
            raise HarnessUsageLimit(
                f"Model usage allowance unavailable; next check {state['retry_at_kst']} KST")


def record_usage_limit(stderr, log):
    """Recognize CLI error diagnostics, never arbitrary prompt/review text."""
    errors = [line for line in stderr.splitlines()
              if re.match(r"^ERROR:.*hit your usage limit", line, re.IGNORECASE)]
    if not errors:
        return False
    now = time.time()
    retry_after = now + 300
    reset = re.search(r"try again at (\d{1,2}):(\d{2})\s*(AM|PM)\b", errors[-1], re.IGNORECASE)
    korea = timezone(timedelta(hours=9))
    if reset:
        hour, minute = int(reset[1]), int(reset[2])
        if 1 <= hour <= 12 and 0 <= minute < 60:
            hour = hour % 12 + (12 if reset[3].upper() == "PM" else 0)
            candidate = datetime.fromtimestamp(now, korea).replace(
                hour=hour, minute=minute, second=0, microsecond=0).timestamp()
            if candidate > now:
                retry_after = candidate + 5
    COOLDOWN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if COOLDOWN_PATH.exists():
        previous = json.loads(COOLDOWN_PATH.read_text(encoding="utf-8"))
        retry_after = max(retry_after, previous["retry_after"])
    state = {"retry_after": retry_after,
             "retry_at_kst": datetime.fromtimestamp(retry_after, korea).isoformat(),
             "source_log": str(log), "reason": "CLI reported usage limit"}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=COOLDOWN_PATH.parent,
                                     prefix="cooldown-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(state, stream)
    try:
        temporary.replace(COOLDOWN_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def find_codex():
    """Support normal terminals as well as the VS Code extension environment."""
    override = os.environ.get("CODEX_EXECUTABLE")
    if override:
        executable = shutil.which(override)
        if executable:
            return executable
        raise RuntimeError("CODEX_EXECUTABLE does not name an available executable")
    executable = shutil.which("codex")
    if executable:
        return executable
    # VS Code exposes its bundled CLI on PATH only in some child environments.
    extensions = Path.home() / ".vscode" / "extensions"
    candidates = list(extensions.glob("openai.chatgpt-*/bin/windows-x86_64/codex.exe"))
    if candidates:
        return str(max(candidates, key=lambda path: path.stat().st_mtime))
    raise RuntimeError("Codex not found. Add it to PATH or set CODEX_EXECUTABLE to its full path.")


def call_harness(prompt, timeout=90):
    check_usage_cooldown()
    executable = find_codex()
    log_dir = BASE / "development_logs"
    log_dir.mkdir(exist_ok=True)
    command = [executable, "exec", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", "--color", "never",
               "-c", "project_doc_max_bytes=0", "-"]
    deadline = time.monotonic() + timeout
    for attempt in range(4):
        check_usage_cooldown()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Harness retry time budget exhausted")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        log = log_dir / f"harness_{stamp}_attempt{attempt + 1}.txt"
        answer, delay = _run_attempt(command, prompt, remaining, log, attempt, deadline)
        if delay is None:
            return answer
        time.sleep(delay)


def _run_attempt(command, prompt, remaining, log, attempt, deadline):
    # Each attempt preserves raw output in its own new file.
    with log.open("x", encoding="utf-8") as capture:
        capture.write("COMMAND: " + json.dumps(command) + "\nPROMPT:\n" + prompt + "\n")
        capture.flush()
        with tempfile.TemporaryDirectory(prefix="harness_", dir=BASE) as work:
            try:
                result = subprocess.run(command, input=prompt, text=True,
                                        encoding="utf-8", errors="replace",
                                        capture_output=True, cwd=work, timeout=remaining)
            except subprocess.TimeoutExpired as error:
                capture.write(f"TIMEOUT: {remaining}s\n")
                for label, value in [("STDOUT", error.stdout), ("STDERR", error.stderr)]:
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    capture.write(f"{label}:\n{value or ''}\n")
                raise
        capture.write(f"EXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n")
        capture.flush()
        if result.returncode:
            if record_usage_limit(result.stderr, log):
                capture.write("PAUSED: account usage limit; shared cooldown recorded\n")
                check_usage_cooldown()
            # Match an HTTP status in diagnostic output, not arbitrary prompt text.
            rate_limited = "429 Too Many Requests" in result.stderr
            if rate_limited and attempt < 3:
                delay = 5 * (2 ** attempt)
                if deadline - time.monotonic() <= delay + 1:
                    capture.write("RETRY STOPPED: total time budget exhausted\n")
                    raise TimeoutError(f"Harness retry time budget exhausted; see {log}")
                capture.write(f"RETRY: HTTP 429; waiting {delay}s before attempt {attempt + 2}\n")
                return None, delay
            raise RuntimeError(f"Harness exited {result.returncode}; see {log}")
        try:
            answer = json.loads(result.stdout)
            if not isinstance(answer, dict):
                raise ValueError("Expected a JSON object")
        except ValueError as error:
            capture.write(f"PARSE ERROR: {error}\n")
            raise
        return answer, None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if not args.smoke:
        parser.error("Use --smoke for the first transport check")
    print(json.dumps(call_harness(
        'Do not use tools. Return only this JSON object: {"status":"ok"}'
    )))
