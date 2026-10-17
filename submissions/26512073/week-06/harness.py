"""Small transport adapter: prompt in, JSON object out; no trading rules yet."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


BASE = Path(__file__).resolve().parent


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
    executable = find_codex()
    log_dir = BASE / "development_logs"
    log_dir.mkdir(exist_ok=True)
    command = [executable, "exec", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", "--color", "never",
               "-c", "project_doc_max_bytes=0", "-"]
    deadline = time.monotonic() + timeout
    for attempt in range(4):
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
