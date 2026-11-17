"""Calls the harness (Claude Code, headless) and returns its answer as a dict.

Every decision of the seller and the buyer goes through ask(). The harness runs in
an empty temporary directory with no tools and no settings, so it sees only the
prompt we give it. The answer must be one JSON object; we strip ```json fences.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

MODEL = os.environ.get("HARNESS_MODEL", "haiku")
CLAUDE = shutil.which("claude") or "claude"
EMPTY_DIR = tempfile.mkdtemp(prefix="w06-harness-")
STRATEGY_MODEL = os.environ.get("STRATEGY_MODEL", "sonnet")   # week 08: the strategist's model


def cmd(model):
    return [CLAUDE, "-p", "--model", model, "--tools", "", "--setting-sources", "", "--no-session-persistence"]


CMD = cmd(MODEL)
CALLS = 0


def _parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def ask(system, prompt, log, tag="harness", retries=2, timeout=120, model=None):
    """Send one prompt; return the parsed JSON object, or None after retries. model: haiku unless given."""
    global CALLS
    for attempt in range(1 + retries):
        CALLS += 1
        t0 = time.time()
        try:
            p = subprocess.run(cmd(model or MODEL) + ["--system-prompt", system], input=prompt, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", cwd=EMPTY_DIR, timeout=timeout)
            raw = (p.stdout or "").strip()
        except subprocess.TimeoutExpired:
            raw = ""
        dt = time.time() - t0
        log(f"[{tag}] call #{CALLS} ({dt:.1f}s) prompt:\n{prompt}\n[{tag}] raw answer: {raw[:600]}")
        out = _parse(raw)
        if isinstance(out, dict):
            return out
        log(f"[{tag}] could not parse JSON (attempt {attempt + 1})")
    return None
