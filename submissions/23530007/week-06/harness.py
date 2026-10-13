"""Calls the harness (Claude Code, non-interactive) and reads one JSON object from its answer.

The harness runs in a fresh empty directory so it picks up no project instructions, gets the
prompt on stdin, and has no tools. Both agents use this module; neither contains a model of its own.

  HARNESS_CMD   override the command (default: claude -p with haiku, no tools, no settings)
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time

HARNESS_CMD = os.environ.get(
    "HARNESS_CMD",
    "claude -p --model haiku --tools '' --setting-sources '' --no-session-persistence")
TIMEOUT = 90

CALLS = {"n": 0}


def log(who: str, text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{who}] {text}", flush=True)


def decide(who: str, prompt: str) -> dict | None:
    """Send `prompt` to the harness; return the last JSON object in its answer, or None.

    One retry if the answer has no JSON object. The caller decides what None means.
    """
    for attempt in (1, 2):
        CALLS["n"] += 1
        with tempfile.TemporaryDirectory() as empty:
            try:
                proc = subprocess.run(shlex.split(HARNESS_CMD), input=prompt, cwd=empty,
                                      capture_output=True, text=True, timeout=TIMEOUT)
                raw = proc.stdout.strip()
            except subprocess.TimeoutExpired:
                raw = ""
                log(who, f"harness call #{CALLS['n']} timed out after {TIMEOUT} s")
        log(who, f"harness call #{CALLS['n']} -> {raw[:300]!r}")
        found = re.findall(r"\{[^{}]*\}", raw)
        for chunk in reversed(found):
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
        log(who, f"no JSON object in the answer (attempt {attempt})")
    return None


def as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    if isinstance(v, str) and re.fullmatch(r"\s*\d+(\.\d+)?\s*", v):
        return int(round(float(v)))
    return None


if __name__ == "__main__":     # quick manual check: python harness.py
    print(decide("check", 'Reply with exactly {"ok": 1} and nothing else.'), file=sys.stderr)
