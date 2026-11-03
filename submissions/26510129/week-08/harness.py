"""The one place that talks to a tightly bounded Claude Haiku harness.

    decide(system, situation) -> dict    parsed from the one-line JSON the model answers

The model only proposes a compact JSON decision. Deterministic code still owns the
budget, inventory, receipt checks, and signing authority. Calls are shared-budgeted
through .market/llm_quota.json; once the daily limit is reached, callers use their
deterministic fallback instead of consuming more model quota.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MODEL = "haiku"
DAILY_CALL_LIMIT = 200
MAX_BUDGET_USD = "0.002"
TIMEOUT = 30          # the kit stops a buyer after 300 s; fail closed instead of blocking it
RETRIES = 0           # malformed output falls back; never pay for a repair call
KST = ZoneInfo("Asia/Seoul")
EMPTY_DIR = tempfile.mkdtemp(prefix="a2a-harness-")
CMD = [
    "claude", "-p", "--model", MODEL,
    "--effort", "low",
    "--max-budget-usd", MAX_BUDGET_USD,
    "--tools", "",
    "--setting-sources", "",
    "--safe-mode",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--permission-prompts", "none",
    "--prompt-suggestions", "false",
    "--no-session-persistence",
]


def _env():
    # Claude Code refuses to start inside another Claude Code session.
    return {k: v for k, v in os.environ.items()
            if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}


def _daily_limit():
    try:
        return max(0, int(os.environ.get("MARKET_LLM_DAILY_LIMIT", DAILY_CALL_LIMIT)))
    except ValueError:
        return DAILY_CALL_LIMIT


def _claim_daily_quota(now=None):
    """Atomically reserve one model call across the kit's short-lived buyer processes."""
    limit = _daily_limit()
    if limit == 0:
        return None
    market_dir = Path(os.environ.get("MARKET_DIR", ".market"))
    state_path = market_dir / "llm_quota.json"
    lock_path = market_dir / "llm_quota.lock"
    today = (now or datetime.now(KST)).astimezone(KST).date().isoformat()
    try:
        market_dir.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            used = int(state.get("used", 0)) if state.get("date") == today else 0
            if used >= limit:
                return None
            used += 1
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"date": today, "used": used, "limit": limit}) + "\n", encoding="utf-8")
            os.replace(tmp, state_path)
            return used, limit
    except OSError as exc:
        print(f"[harness] quota state unavailable: {exc}; treated as no answer", file=sys.stderr, flush=True)
        return None


def call(system: str, situation: str) -> str:
    t0, size = time.time(), len(system) + len(situation)
    quota = _claim_daily_quota()
    if quota is None:
        print(f"[harness] daily Haiku quota exhausted or disabled ({_daily_limit()}/day); "
              "treated as no answer", file=sys.stderr, flush=True)
        return ""
    bounded_system = system + ("\nReturn exactly one compact JSON object, with no markdown or explanation. "
                               "Keep the entire answer under 120 characters.")
    try:
        result = subprocess.run(
            CMD + ["--system-prompt", bounded_system],
            input=situation,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            cwd=EMPTY_DIR,
            env=_env(),
        )
    except subprocess.TimeoutExpired:
        print(f"[harness] Haiku timed out after {TIMEOUT}s in={size}ch; treated as no answer",
              file=sys.stderr, flush=True)
        return ""
    out = result.stdout.strip()
    print(f"[harness] haiku call={quota[0]}/{quota[1]} {time.time() - t0:.1f}s "
          f"exit={result.returncode} in={size}ch -> {out[:160]!r}",
          file=sys.stderr, flush=True)
    if result.returncode != 0:
        print(f"[harness] stderr: {result.stderr.strip()[:240]}", file=sys.stderr, flush=True)
    return out


def parse(text: str, need: str = "action"):
    """The first JSON object in the answer that has the key `need`. Nested objects are fine; code fences are ignored."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            d, _ = dec.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and need in d:
            return d
    return None


def decide(system: str, situation: str, need: str = "action") -> dict | None:
    for _ in range(1 + RETRIES):
        d = parse(call(system, situation), need)
        if d is not None:
            if "price" in d:
                try:
                    d["price"] = int(round(float(d["price"])))
                except (TypeError, ValueError):
                    d.pop("price")
            return d
    return None
