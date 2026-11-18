"""Calls the harness (Claude Code, haiku) with a prompt on stdin and returns its JSON answer.

Shared by seller.py and buyer.py. The harness runs in an empty directory so it
does not pick up any project's CLAUDE.md or settings.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time

HARNESS = ["claude", "-p", "--model", "haiku", "--tools", "", "--setting-sources", "", "--no-session-persistence"]
EMPTY_DIR = tempfile.mkdtemp(prefix="week06-harness-")
TIMEOUT = 90  # seconds for one harness call
MAX_PROMPT = 60_000  # characters: a prompt longer than this was stuffed by someone else's text, do not send it
# Without ANTHROPIC_API_KEY, claude -p uses the logged-in subscription instead of billing the API key.
ENV = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def log(tag, text):
    # the date too (11/9: twice an old day's line read as today's: the log has days of lines and no date)
    print(f"[{time.strftime('%m/%d %H:%M:%S')}] {tag}: {text}", file=sys.stderr, flush=True)


def clip(text, n):
    """Text from another agent (a shop's reply, a product or shop name, a forum post) made safe to quote: control
    characters dropped, whitespace collapsed, at most n characters. Round 3 lets agents write first (the forum,
    unasked replies), so their text must not be able to fill our model's context or our logs."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", str(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def parse_json(text):
    """The first {...} object in the answer, or None."""
    start = text.find("{")
    if start >= 0:
        # a whole object, nested ones included (meta_shop.py asks for {"open": {...}, ...})
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*?\}", text, re.S)
    if m is None:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


async def ask(prompt, tries=2, model=None, timeout=TIMEOUT, max_chars=MAX_PROMPT):
    """Run the harness once (retry once on failure). Returns a dict, or None if every try failed.
    `model` swaps haiku for another model (meta_explore.py uses sonnet). A prompt over `max_chars` is not sent
    (None, as if the harness failed): the caller's own guards then decide, and a context bomb costs nothing."""
    if len(prompt) > max_chars:
        log("HARNESS", f"prompt of {len(prompt)} characters is over {max_chars}: not sent")
        return None
    cmd = HARNESS if model is None else [model if a == "haiku" else a for a in HARNESS]
    for attempt in range(1, tries + 1):
        started = time.time()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=EMPTY_DIR, env=ENV,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            log("HARNESS", f"try {attempt}: timeout after {timeout} s")
            continue
        answer = out.decode().strip()
        log("HARNESS", f"try {attempt}: {time.time() - started:.1f} s, exit {proc.returncode}, answer {answer!r}")
        if proc.returncode != 0:
            log("HARNESS", f"stderr {err.decode().strip()[:300]!r}")
            continue
        data = parse_json(answer)
        if data is not None:
            return data
    return await fallback(prompt)


# GPT through the user's ChatGPT plan, called with codex directly and the model pinned (11/12: Hermes followed its own
# default model, so a change there changed our reviewer unseen); the user's config and AGENTS.md are left out
GPT = ["codex", "exec", "-m", "gpt-6.1-sol", "--skip-git-repo-check", "-s", "read-only", "--ephemeral",
       "--ignore-user-config", "-c", "project_doc_max_bytes=0"]
# Copilot (Student plan: only Copilot Fusion, which picks the model and may pick Claude), a separate usage limit from
# ChatGPT's, so it is the last fallback where any answer will do, never the second-opinion reviewer
COPILOT = ["copilot", "-s", "--no-custom-instructions", "-p"]
FALLBACK = [GPT, COPILOT]


async def fallback(prompt, timeout=60):
    """When every Claude try failed (a usage limit, an outage), the same prompt once to GPT through Hermes: the
    agents keep deciding instead of stopping (11/4: another student's agents stopped when their tokens ran out).
    HARNESS_FALLBACK=0 turns it off; returns None when GPT fails too."""
    if os.environ.get("HARNESS_FALLBACK", "1") == "0":
        return None
    for cmd in FALLBACK:
        started = time.time()
        out = await run_cli(cmd, prompt[:20000], timeout)
        data = parse_json(out) if out else None
        who = "GPT" if cmd is GPT else "Copilot"
        log("HARNESS", f"fell back to {who}: {time.time() - started:.1f} s, {'an answer' if data else 'no answer'}"
            if out is not None else f"fallback to {who} failed")
        if data is not None:
            return data
    return None


async def run_cli(cmd, prompt, timeout):
    """stdout of one CLI call (stdin closed: codex reads a piped stdin as more prompt), or None on failure."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, prompt, cwd=EMPTY_DIR, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return out.decode(errors="replace")
    except (OSError, asyncio.TimeoutError):
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return None
