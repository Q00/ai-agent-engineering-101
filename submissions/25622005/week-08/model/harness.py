"""One call to a coding harness in non-interactive mode: prompt on stdin, answer on stdout.

    HARNESS_CMD   the shell command to run (default below: Claude Code, Sonnet 5.5, no tools)
A caller may pass its own command (the strategist and the negotiation fallback use different models).

Checked commands (2026-10-13):
    claude -p --model claude-sonnet-5-5 --tools "" --setting-sources "" --no-session-persistence
    codex exec --skip-git-repo-check -
Claude Code 2.1.112 (the N36L's pinned CLI, 2026-11-03, against a stub API) sends a full model ID such
as claude-opus-5-5 as given, but maps the alias "opus" to claude-opus-4-7: name models by full ID.

The harness runs in an empty temporary directory so it cannot pick up a project's
CLAUDE.md or AGENTS.md, and the buyer and seller never share a session.
"""
import json
import os
import re
import subprocess
import tempfile

DEFAULT_CMD = 'claude -p --model claude-sonnet-5-5 --tools "" --setting-sources "" --no-session-persistence'
CMD = os.environ.get("HARNESS_CMD", DEFAULT_CMD)
_CWD = tempfile.mkdtemp(prefix="harness-")


def ask(prompt: str, timeout: int = 180, cmd: str | None = None) -> str:
    out = subprocess.run(cmd or CMD, shell=True, input=prompt, capture_output=True, text=True,
                         cwd=_CWD, timeout=timeout, env=dict(os.environ, MAX_THINKING_TOKENS="0"))
    if out.returncode != 0:          # Claude Code prints its errors (login, model access) on stdout
        raise RuntimeError(f"harness exit {out.returncode}: {(out.stderr.strip() or out.stdout.strip())[:200]}")
    return out.stdout.strip()


def first_json_object(raw: str) -> dict | None:
    """The first complete JSON object in the text, nested objects included."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", raw):
        try:
            obj, _ = dec.raw_decode(raw, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def ask_json(prompt: str, timeout: int = 180, cmd: str | None = None) -> tuple[dict | None, str]:
    """The first {...} object in the answer, and the raw answer for the log."""
    raw = ask(prompt, timeout, cmd)
    return first_json_object(raw), raw
