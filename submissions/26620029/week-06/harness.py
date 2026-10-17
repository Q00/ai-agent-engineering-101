"""Calls the harness (Claude Code, non-interactive) and parses a JSON answer.

Shared by seller.py and buyer.py. Each call:
  - runs `claude -p` in an empty temp directory, so no CLAUDE.md/AGENTS.md of
    this repository leaks into the prompt;
  - sends the prompt on stdin, reads stdout;
  - extracts the last {...} object from the answer (the model sometimes wraps
    it in prose or a ```json fence);
  - retries once on timeout / unparseable output, then returns None so the
    caller can fall back to a safe move (the caller decides what is safe).
"""
import json
import re
import subprocess
import sys
import tempfile
import time

HARNESS_CMD = ["claude", "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
TIMEOUT = 90        # seconds per call; the kit buyer waits 200 s per message
_EMPTY_DIR = tempfile.mkdtemp(prefix="w06-harness-")

CALLS = 0           # counted for the report


def log(*a):
    print(*a, flush=True, file=sys.stdout)


def _extract_json(text: str):
    """Last JSON object in the text, or None."""
    for m in reversed(list(re.finditer(r"\{[^{}]*\}", text, flags=re.S))):
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            continue
    return None


def ask_json(prompt: str, tag: str = "harness", tries: int = 2):
    """Send prompt to the harness, return (dict | None, raw_text)."""
    global CALLS
    raw = ""
    for attempt in range(1, tries + 1):
        CALLS += 1
        t0 = time.time()
        try:
            p = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                               cwd=_EMPTY_DIR, timeout=TIMEOUT)
            raw = (p.stdout or "").strip()
            if p.returncode != 0:
                log(f"[{tag}] call {CALLS} exit {p.returncode}: {(p.stderr or '')[:200]}")
        except subprocess.TimeoutExpired:
            log(f"[{tag}] call {CALLS} timed out after {TIMEOUT}s (attempt {attempt})")
            continue
        except FileNotFoundError:
            log(f"[{tag}] harness binary not found: {HARNESS_CMD[0]}")
            return None, ""
        data = _extract_json(raw)
        log(f"[{tag}] call {CALLS} ({time.time() - t0:.1f}s) -> {raw[:300]!r}")
        if data is not None:
            return data, raw
        log(f"[{tag}] no JSON in answer (attempt {attempt})")
    return None, raw
