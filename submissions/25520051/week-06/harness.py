"""Calls the harness (Claude Code, non-interactive) and returns its answer as a dict.

Both the seller and the buyer use this. The harness runs in an empty temp
directory so it does not pick up this repository's CLAUDE.md / AGENTS.md.
"""
import json
import re
import subprocess
import tempfile

MODEL = "haiku"
CMD = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
       "--no-session-persistence", "--system-prompt"]
EMPTY_DIR = tempfile.mkdtemp(prefix="harness-")


def ask_json(system: str, prompt: str, timeout: int = 60, attempts: int = 2) -> dict | None:
    """Ask up to `attempts` times; return the first JSON object in an answer, or None.

    One timed-out call once made the buyer walk away from a deal it could have made
    (logs/buyer-under-test-buyer_deal-3.log, first trade test run), hence the retry.
    """
    for i in range(attempts):
        ans = _ask_once(system, prompt, timeout)
        if ans is not None:
            return ans
        print(f"[harness] no usable answer (attempt {i + 1}/{attempts})", flush=True)
    return None


def _ask_once(system: str, prompt: str, timeout: int) -> dict | None:
    try:
        out = subprocess.run(CMD + [system], input=prompt, capture_output=True, text=True,
                             encoding="utf-8", timeout=timeout, cwd=EMPTY_DIR, shell=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[harness] call failed: {e!r}", flush=True)
        return None
    raw = out.stdout.strip()
    print(f"[harness] raw: {raw!r}", flush=True)
    m = re.search(r"\{.*\}", raw, re.S)   # haiku often wraps JSON in ```json fences
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None
