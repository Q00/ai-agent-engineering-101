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
# Without ANTHROPIC_API_KEY, claude -p uses the logged-in subscription instead of billing the API key.
ENV = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}


def log(tag, text):
    print(f"[{time.strftime('%H:%M:%S')}] {tag}: {text}", file=sys.stderr, flush=True)


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


async def ask(prompt, tries=2, model=None, timeout=TIMEOUT):
    """Run the harness once (retry once on failure). Returns a dict, or None if every try failed.
    `model` swaps haiku for another model (meta_explore.py uses sonnet)."""
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
    return None
