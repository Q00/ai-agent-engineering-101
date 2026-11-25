"""The one place that talks to a tightly bounded Claude Haiku harness.

    decide(system, situation) -> dict    parsed from the one-line JSON the model answers

The model only proposes a compact JSON decision. Deterministic code still owns the
budget, inventory, receipt checks, and signing authority. There is no call quota: each
call is kept small instead (no tools, no retry, a short answer, a per-call dollar cap).
When the subscription limit is hit or the answer is unusable, callers use their
deterministic fallback.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

MODEL = "haiku"
MAX_BUDGET_USD = "0.005"  # measured 2026-11-03: a 4.7k-token shop choice cost $0.0016; this only stops a runaway call
TIMEOUT = 30          # the kit stops a buyer after 300 s; fail closed instead of blocking it
RETRIES = 0           # malformed output falls back; never pay for a repair call
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
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}
    # 11-04 00:00: the cron-run tuner failed with "OAuth session expired" because the login on this machine lives in
    # CLAUDE_CONFIG_DIR (~/.claude-jiseok), which cron and a plain ssh shell do not set. Default to it when it exists.
    alt = os.path.join(os.path.expanduser("~"), ".claude-jiseok")
    if "CLAUDE_CONFIG_DIR" not in env and os.path.isdir(alt):
        env["CLAUDE_CONFIG_DIR"] = alt
    return env


def call(system: str, situation: str) -> str:
    t0, size = time.time(), len(system) + len(situation)
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
    print(f"[harness] haiku {time.time() - t0:.1f}s "
          f"exit={result.returncode} in={size}ch -> {out[:160]!r}",
          file=sys.stderr, flush=True)
    if result.returncode != 0:
        print(f"[harness] stderr: {result.stderr.strip()[:240]}", file=sys.stderr, flush=True)
        _alarm(out or result.stderr.strip())
    return out


def _alarm(reason):
    """A model call that failed is recorded as an alert, at most once in six hours. 11-15 21:48 the subscription login
    expired; 1,150 calls failed over two days, the code fallback kept trading, and nothing told anyone."""
    try:
        import playbook
        entry = playbook.alert_once("model-call-failed", "A bargaining model call failed; the code fallback is trading "
                                    "in its place until this is fixed.", scope="model", evidence=reason[:160])
        if entry:
            print(f"[alert] model call failed: {reason[:120]}", file=sys.stderr, flush=True)
    except Exception as e:   # the alarm must never break an order
        print(f"[harness] alert not recorded: {type(e).__name__}", file=sys.stderr, flush=True)


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
