"""The one place that talks to the harness (Claude Code, haiku).

    decide(system, situation) -> dict    parsed from the one-line JSON the model answers

The role and the rules go in --system-prompt (haiku refuses a "you are a shop" that
arrives as the user message; see REPORT.md part 3). The current situation goes in on
stdin. The command is the one from the week-06 notes, run in an empty directory so
no CLAUDE.md or AGENTS.md is read.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

CMD = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
TIMEOUT = 90          # seconds per harness call
RETRIES = 2           # extra attempts when the answer is not one JSON object

EMPTY_DIR = tempfile.mkdtemp(prefix="a2a-harness-")


def _env():
    # Claude Code refuses to start inside another Claude Code session; the trade test may be run from one.
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}


def call(system: str, situation: str) -> str:
    t0, size = time.time(), len(system) + len(situation)
    r = subprocess.run(CMD + ["--system-prompt", system], input=situation, capture_output=True, text=True,
                       timeout=TIMEOUT, cwd=EMPTY_DIR, env=_env())
    out = r.stdout.strip()
    print(f"[harness] {time.time() - t0:.1f}s exit {r.returncode} in={size}ch -> {out[:200]!r}", file=sys.stderr, flush=True)
    if r.returncode != 0:
        print(f"[harness] stderr: {r.stderr.strip()[:300]}", file=sys.stderr, flush=True)
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
