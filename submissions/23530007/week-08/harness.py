"""Calls the harness (Claude Code, non-interactive) and reads one JSON object from its answer.

The harness runs in a fresh empty directory so it picks up no project instructions, gets the
prompt on stdin, and has no tools. Both agents use this module; neither contains a model of its own.

  HARNESS_CMD   override the command (default: claude -p with haiku, no tools, no settings)

Circuit breaker (Round 3, nobody watching): when a call gets no JSON on both attempts (timeout, harness missing,
login expired, rate limit, error text), every agent skips the harness for OPEN_FOR seconds and its code rules
answer instead. The state is one file shared by the seller and every buyer run; the first call after OPEN_FOR
tries the harness again and closes the breaker if it answers.
  HARNESS_BREAKER  the breaker file (default .market/harness_down next to this file)
"""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import model_env
import sys
import tempfile
import time

HARNESS_CMD = os.environ.get(
    "HARNESS_CMD",
    "claude -p --model haiku --effort low --max-budget-usd 0.02 --tools '' --setting-sources '' --no-session-persistence")
TIMEOUT = 45
BREAKER = Path(os.environ.get("HARNESS_BREAKER", Path(__file__).resolve().parent / ".market" / "harness_down"))
OPEN_FOR = 600


def breaker_open() -> bool:
    try:
        return time.time() < float(BREAKER.read_text())
    except (OSError, ValueError):
        return False


def trip(who: str, why: str) -> None:
    try:
        BREAKER.parent.mkdir(exist_ok=True)
        BREAKER.write_text(str(time.time() + OPEN_FOR))
    except OSError:
        pass
    log(who, f"harness breaker OPEN for {OPEN_FOR} s ({why}); code rules answer until then")


def close_breaker() -> None:
    try:
        BREAKER.unlink()
    except OSError:
        pass

CALLS = {"n": 0}


def log(who: str, text: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{who}] {text}", flush=True)


def decide(who: str, prompt: str, timeout: float | None = None) -> dict | None:
    """Send `prompt` to the harness; return the last JSON object in its answer, or None.

    One retry if the answer has no JSON object. The caller decides what None means.
    With `timeout` (a fast move, e.g. one haggling step): one attempt of at most that long, and a slow answer
    only returns None for this move (the caller's code rule answers) without opening the breaker. 11-05: haiku
    answered a buyer move in 4 s (median) but 18 s at p90, and every second holds the next order back.
    """
    if breaker_open():
        return None
    for attempt in ((1,) if timeout else (1, 2)):
        CALLS["n"] += 1
        with tempfile.TemporaryDirectory() as empty:
            try:
                proc = model_env.run(shlex.split(HARNESS_CMD), prompt, empty, timeout or TIMEOUT, role="haiku")   # API key, else login
                raw = proc.stdout.strip()
            except subprocess.TimeoutExpired:
                raw = ""
                log(who, f"harness call #{CALLS['n']} timed out after {timeout or TIMEOUT} s")
                if timeout:
                    return None
            except OSError as e:          # harness not installed / not on PATH: the caller falls back
                log(who, f"harness could not start: {e!r}")
                trip(who, "could not start")
                return None
        log(who, f"harness call #{CALLS['n']} -> {raw[:300]!r}")
        obj = last_json_object(raw)
        if obj is not None:
            if BREAKER.exists():
                close_breaker()
                log(who, "harness answers again; breaker closed")
            return obj
        log(who, f"no JSON object in the answer (attempt {attempt})")
    trip(who, f"no JSON twice, last answer {raw[:80]!r}")
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
