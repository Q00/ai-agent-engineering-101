"""Week 06: the harness that makes every decision for the seller and the buyer.

The harness is Claude Code in non-interactive mode (`claude -p`), the command the
README checked on 2026-10-13. The prompt goes in on stdin. Every call is a fresh
session, so the caller puts the whole negotiation so far into the prompt.

The model is asked to answer with one JSON object. `ask_json` pulls it out of the
reply text; claude -p has no tool_use blocks, so parsing is our job.
"""
import json
import os
import re
import subprocess
import tempfile
import time

MODEL = os.environ.get("AGENT_MODEL", "haiku")

# --tools ""              block Claude Code's own tools (Read, Bash, ...). --allowed-tools "" does not.
# --setting-sources ""    ignore user and project settings.
# --no-session-persistence  do not write the session to disk.
# --output-format json    the reply comes wrapped in JSON with usage; we read "result".
CMD = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
       "--no-session-persistence", "--output-format", "json"]

# An empty directory so the harness does not pick up this repository's CLAUDE.md/AGENTS.md.
WORKDIR = tempfile.mkdtemp(prefix="week06-harness-")


class HarnessError(RuntimeError):
    """claude -p failed or its reply had no JSON object in it."""


def call(prompt: str, timeout: int = 150) -> str:
    """One harness call. Returns the model's reply text."""
    proc = subprocess.run(CMD, input=prompt, capture_output=True, text=True,
                          cwd=WORKDIR, timeout=timeout)
    if proc.returncode != 0:
        raise HarnessError(f"claude -p exited {proc.returncode}: {proc.stderr[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise HarnessError(f"claude -p gave non-JSON: {proc.stdout[:300]!r}")
    if payload.get("is_error"):
        raise HarnessError(f"claude -p error: {str(payload.get('result'))[:300]}")
    return payload.get("result") or ""


def ask_json(prompt: str, tag: str, tries: int = 2) -> dict:
    """Call the harness and return the first JSON object in its reply.

    Every call is printed (stdout goes to the trade test's logs), so a wrong
    decision can be traced back to the exact reply that caused it.
    """
    for attempt in range(1, tries + 1):
        t0 = time.time()
        reply = call(prompt)
        print(f"[harness {tag}] {time.time() - t0:.1f}s reply: {reply.strip()[:300]}", flush=True)
        m = re.search(r"\{.*?\}", reply, re.S)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        print(f"[harness {tag}] PARSE_FAIL attempt {attempt}/{tries}", flush=True)
    raise HarnessError(f"no JSON object after {tries} tries")
