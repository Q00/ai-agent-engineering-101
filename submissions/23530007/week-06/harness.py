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
            except OSError as e:          # harness not installed / not on PATH: the caller falls back
                log(who, f"harness could not start: {e!r}")
                return None
        log(who, f"harness call #{CALLS['n']} -> {raw[:300]!r}")
        obj = last_json_object(raw)
        if obj is not None:
            return obj
        log(who, f"no JSON object in the answer (attempt {attempt})")
    return None


KEEP_RECENT = 4     # moves kept word for word; older ones are compacted into one line


def compact(history: list, who: str = "") -> str:
    """Context compaction for the prompt. `history` is [(label, price), ...], oldest first.

    The last KEEP_RECENT moves stay as they are. Everything before them becomes one summary line
    with the move count and how each side's price moved (first -> last), so the prompt stays short
    however long the negotiation runs, and the trend survives.
    """
    if not history:
        return "- (nothing yet)"
    recent = [f"- {w}: {p}" for w, p in history[-KEEP_RECENT:]]
    older = history[:-KEEP_RECENT]
    if not older:
        return "\n".join(recent)
    trend = {}
    for w, p in older:
        trend.setdefault(w, []).append(p)
    parts = "; ".join(f"{w} {v[0]}" + (f" -> {v[-1]}" if len(v) > 1 else "") for w, v in trend.items())
    if who:
        log(who, f"context compaction: {len(older)} older moves -> 1 line, {len(recent)} kept")
    return "\n".join([f"- (earlier, {len(older)} moves compacted) {parts}"] + recent)


def last_json_object(raw: str):
    """The last complete top-level JSON object in the text, nested objects included.

    The first version took the last brace pair without any brace inside it. For a nested
    answer like {"plan": [{...}, {...}]} that returned the last inner object, so challenge 2's plan
    was silently dropped and the buyer fell back to trying every shop in order (attempt 1).
    """
    dec, found, i = json.JSONDecoder(), None, 0
    while (i := raw.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            found = obj
        i = end
    return found


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
