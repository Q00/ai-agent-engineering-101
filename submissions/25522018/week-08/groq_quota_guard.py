"""Cross-process Groq 429 circuit breaker for Week 8 seller and buyer.

Primary decision policy is still the real Groq model. On HTTP 429, skip
retries, respect Retry-After, and let the caller's explicit heuristic fallback
handle the negotiation. This file makes no API calls itself.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

import llm_harness
MODEL = llm_harness.MODEL

STATE_FILE = Path(__file__).resolve().parent / ".market" / "groq_cooldown.json"


def _read_cooldown(now: float) -> float:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("model") != MODEL:
            return 0.0
        until = float(data.get("until", 0))
        return until if math.isfinite(until) and until > now else 0.0
    except (OSError, ValueError, TypeError, OverflowError):
        return 0.0


def _rate_limit_info(exc: BaseException):
    """Return (reason, cooldown_seconds) only for HTTP 429.

    Use the complete response body (not the truncated error string) to
    distinguish TPD from other throttling; never print or store the body.
    """
    response = None
    cur = exc
    for _ in range(8):
        maybe = getattr(cur, "response", None)
        if getattr(maybe, "status_code", None) == 429:
            response = maybe
            break
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        if cur is None:
            break
    text = str(exc)
    if response is None and not re.search(r"Groq HTTP 429|\bHTTP 429\b", text, re.I):
        return None
    detail = getattr(response, "text", "") if response is not None else text
    reason = "TPD" if re.search(r"tokens\s+per\s+day|\bTPD\b", detail, re.I) else "429"
    headers = getattr(response, "headers", {}) if response is not None else {}
    raw_retry = headers.get("retry-after") if headers else None
    if not raw_retry:
        m = re.search(r"retry-after\s*=\s*([0-9.]+)", text, re.I)
        raw_retry = m.group(1) if m else None
    try:
        seconds = float(raw_retry)
    except (TypeError, ValueError, OverflowError):
        seconds = 600.0 if reason == "TPD" else 30.0
    if not math.isfinite(seconds) or seconds < 0:
        seconds = 600.0 if reason == "TPD" else 30.0
    return reason, min(3600.0, max(5.0, seconds + 3.0))


def _save_cooldown(now: float, seconds: float, reason: str):
    """Write shared state atomically; no credentials or model messages stored."""
    data = {"model": MODEL, "until": now + seconds, "reason": reason}
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_name(STATE_FILE.name + "." + uuid.uuid4().hex + ".tmp")
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"[quota-guard] could not save cooldown: {type(e).__name__}", file=sys.stderr, flush=True)


def decide(system: str, situation: dict, allowed: list[str], *, retries_override: int | None = None) -> dict:
    """Attempt Groq once; raise a recognizable 429 for callers' safe fallback."""
    if os.getenv("GROQ_GUARD_DISABLE", "").strip() == "1":
        return llm_harness.decide(system, situation, allowed, retries_override=retries_override)
    now = time.time()
    until = _read_cooldown(now)
    if until:
        seconds = math.ceil(until - now)
        print(f"[quota-guard] Groq cooldown active: {seconds}s; using caller fallback", file=sys.stderr, flush=True)
        raise RuntimeError(f"Groq HTTP 429; shared quota cooldown active for {seconds}s")
    try:
        # One request, no sleep/retry storm. Caller decides model-free fallback.
        return llm_harness.decide(system, situation, allowed, retries_override=1)
    except RuntimeError as exc:
        info = _rate_limit_info(exc)
        if info is None:
            raise
        reason, seconds = info
        _save_cooldown(now, seconds, reason)
        print(f"[quota-guard] Groq {reason} rate limit; cooldown {seconds:.0f}s; using caller fallback", file=sys.stderr, flush=True)
        raise RuntimeError(f"Groq HTTP 429; {reason} cooldown for {seconds:.0f}s") from exc
