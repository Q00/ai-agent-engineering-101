"""Calls the harness and returns its answer as a dict. The seller and both buyers use this.

Two backends, chosen per call:
- OpenAI (gpt-5-mini) when OPENAI_API_KEY is set, in the environment or in .env next to this file.
  This is what the always-on deployment uses. .env is git-ignored; .env.example shows the format.
- Claude Code (claude -p --model haiku) otherwise, as in week 6. It runs in an empty temp directory
  so it does not pick up this repository's CLAUDE.md / AGENTS.md.

Every caller survives a None answer (the code keeps a plan of its own), so a missing key or an
outage slows the agents down to their rules instead of stopping them.
"""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
CLAUDE_MODEL = "haiku"
CMD = ["claude", "-p", "--model", CLAUDE_MODEL, "--tools", "", "--setting-sources", "",
       "--no-session-persistence", "--system-prompt"]
EMPTY_DIR = tempfile.mkdtemp(prefix="harness-")


def _load_env(path: Path = Path(__file__).with_name(".env")):
    """KEY=VALUE lines; a non-empty environment value wins over the file.

    An empty value does not: the PC's seller worker kept calling claude (not installed there) after the
    key went into .env, because its environment already held the name with no value."""
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = (x.strip() for x in line.split("=", 1))
                v = v.strip('"').strip("'")
                if v and not os.environ.get(k):
                    os.environ[k] = v
    except (OSError, UnicodeDecodeError):
        pass


_load_env()


def backend() -> str:
    return f"openai {OPENAI_MODEL}" if os.environ.get("OPENAI_API_KEY") else f"claude -p {CLAUDE_MODEL}"


def ask_json(system: str, prompt: str, timeout: int = 60, attempts: int = 2) -> dict | None:
    """Ask up to `attempts` times; return the first JSON object in an answer, or None.

    One timed-out call once made the buyer walk away from a deal it could have made
    (logs/buyer-under-test-buyer_deal-3.log, first trade test run), hence the retry.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        _load_env()                      # a key added to .env later takes effect without a restart
    for i in range(attempts):
        raw = _openai(system, prompt, timeout) if os.environ.get("OPENAI_API_KEY") else _claude(system, prompt, timeout)
        ans = _parse(raw)
        if ans is not None:
            return ans
        print(f"[harness] no usable answer (attempt {i + 1}/{attempts})", flush=True)
    return None


def _openai(system: str, prompt: str, timeout: int) -> str:
    body = {"model": OPENAI_MODEL, "reasoning_effort": "minimal",          # gpt-5 models take no temperature
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    try:
        r = httpx.post(OPENAI_URL, json=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
        if r.status_code != 200:
            print(f"[harness] openai {r.status_code}: {r.text[:200]}", flush=True)
            return ""
        raw = r.json()["choices"][0]["message"]["content"] or ""
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        print(f"[harness] openai call failed: {e!r}", flush=True)
        return ""
    print(f"[harness] raw: {raw!r}", flush=True)
    return raw


def _claude(system: str, prompt: str, timeout: int) -> str:
    try:
        out = subprocess.run(CMD + [system], input=prompt, capture_output=True, text=True,
                             encoding="utf-8", timeout=timeout, cwd=EMPTY_DIR, shell=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"[harness] call failed: {e!r}", flush=True)
        return ""
    raw = out.stdout.strip()
    print(f"[harness] raw: {raw!r}", flush=True)
    return raw


def _parse(raw: str) -> dict | None:
    m = re.search(r"\{.*\}", raw or "", re.S)   # haiku often wraps JSON in ```json fences
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None
