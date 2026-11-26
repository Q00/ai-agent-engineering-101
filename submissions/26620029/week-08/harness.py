"""One call to a coding harness in non-interactive mode: prompt on stdin, answer on stdout.

    HARNESS_CMD   the shell command to run (default below: Claude Code, haiku, no tools)

Checked commands (2026-10-13):
    claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence
    codex exec --skip-git-repo-check -

The harness runs in an empty temporary directory so it cannot pick up a project's
CLAUDE.md or AGENTS.md, and the buyer and seller never share a session.
"""
import json
import os
import re
import subprocess
import tempfile

DEFAULT_CMD = 'claude -p --model haiku --tools "" --setting-sources "" --no-session-persistence'
CMD = os.environ.get("HARNESS_CMD", DEFAULT_CMD)
_CWD = tempfile.mkdtemp(prefix="harness-")


class HarnessError(RuntimeError):
    """The harness itself failed (expired login, no usage left, too slow): says nothing about the other party."""


def ask(prompt: str, timeout: int = 180) -> str:
    try:
        out = subprocess.run(CMD, shell=True, input=prompt, capture_output=True, text=True,
                             cwd=_CWD, timeout=timeout, env=dict(os.environ, MAX_THINKING_TOKENS="0"))
    except subprocess.TimeoutExpired as e:
        raise HarnessError(f"harness timeout after {timeout} s") from e
    if out.returncode != 0:
        raise HarnessError(f"harness exit {out.returncode}: {(out.stderr or out.stdout).strip()[:200]}")
    return out.stdout.strip()


def ask_json(prompt: str) -> tuple[dict | None, str]:
    """The first {...} object in the answer, and the raw answer for the log."""
    raw = ask(prompt)
    m = re.search(r"\{.*?\}", raw, re.S)
    if not m:
        return None, raw
    try:
        return json.loads(m.group()), raw
    except json.JSONDecodeError:
        return None, raw
