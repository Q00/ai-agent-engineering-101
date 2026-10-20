"""One call to the harness in non-interactive mode: prompt on stdin, answer on stdout.

    HARNESS_CMD   shell command to run. Default: Claude Code with haiku, no tools, no project settings.

The harness runs in a fresh empty directory, so it never reads a project's CLAUDE.md / AGENTS.md,
and the seller and the buyer never share a session (every call is a new `claude -p` process).
"""
import json
import os
import re
import subprocess
import tempfile
import time

DEFAULT_CMD = 'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence'
CMD = os.environ.get("HARNESS_CMD", DEFAULT_CMD)
_CWD = tempfile.mkdtemp(prefix="harness-")
CALLS = 0


def ask(prompt: str, timeout: int = 180, retries: int = 2) -> str:
    global CALLS
    last = ""
    for attempt in range(retries + 1):
        CALLS += 1
        try:
            out = subprocess.run(CMD, shell=True, input=prompt, capture_output=True, text=True,
                                 encoding="utf-8", cwd=_CWD, timeout=timeout,
                                 env=dict(os.environ, MAX_THINKING_TOKENS="0"))
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
        last = f"exit {out.returncode}: {out.stderr.strip()[:200]}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"harness failed: {last}")


def ask_json(prompt: str) -> tuple[dict | None, str]:
    """The first JSON object in the answer (None if there is none), and the raw answer for the log.
    Decodes from each '{' with raw_decode, so nested objects ({"plan": {...}}) come back whole."""
    raw = ask(prompt)
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            obj, _ = dec.raw_decode(raw[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj, raw
    return None, raw
