"""Week 06 — the harness behind both agents: `claude -p --model haiku`, non-interactive.

Every price decision goes through ask_json(): the prompt goes in on stdin, the
answer comes back on stdout, and we parse the first JSON object in it. The
harness runs in an empty temp directory so it picks up no project instructions.

Resolution of the binary: $CLAUDE_BIN, then `claude` on PATH, then the Claude
Code VS Code extension's bundled binary (this machine).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

MODEL = os.environ.get("HARNESS_MODEL", "haiku")
TIMEOUT = int(os.environ.get("HARNESS_TIMEOUT", "90"))
_EMPTY_DIR = None
CALLS = {"n": 0, "fallbacks": 0, "seconds": 0.0}


def claude_bin() -> str:
    if os.environ.get("CLAUDE_BIN"):
        return os.environ["CLAUDE_BIN"]
    found = shutil.which("claude")
    if found:
        return found
    bundled = sorted(glob.glob(os.path.expanduser(
        "~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude")))
    if bundled:
        return bundled[-1]
    raise FileNotFoundError("claude CLI not found; set CLAUDE_BIN")


COMMAND = f'claude -p --model {MODEL} --tools "" --setting-sources "" --no-session-persistence'


def _empty_dir() -> str:
    global _EMPTY_DIR
    if _EMPTY_DIR is None:
        _EMPTY_DIR = tempfile.mkdtemp(prefix="harness-empty-")
    return _EMPTY_DIR


def ask_text(prompt: str, log=None) -> str:
    """One harness call. Returns stdout text ('' on timeout/error)."""
    cmd = [claude_bin(), "-p", "--model", MODEL, "--tools", "", "--setting-sources", "", "--no-session-persistence"]
    t0 = time.time()
    CALLS["n"] += 1
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=TIMEOUT, cwd=_empty_dir())
        text = out.stdout.strip()
        if out.returncode != 0 and log:
            log(f"    [harness] exit {out.returncode}: {out.stderr.strip()[:200]}")
    except subprocess.TimeoutExpired:
        text = ""
        if log:
            log(f"    [harness] timeout after {TIMEOUT}s")
    CALLS["seconds"] += time.time() - t0
    return text


def parse_json(text: str):
    """First {...} object in the text, or None."""
    text = re.sub(r"```(?:json)?", "", text)
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def ask_json(prompt: str, log=None, retries: int = 1):
    """Harness call expected to answer with one JSON object. None if it never does."""
    for attempt in range(retries + 1):
        text = ask_text(prompt, log)
        data = parse_json(text)
        if log:
            log(f"    [harness] -> {text[:200]!r}" + ("" if data is not None else "  (no JSON)"))
        if data is not None:
            return data
    return None


if __name__ == "__main__":
    print(ask_text(sys.stdin.read()))
