"""Calls the harness (Claude Code, non-interactive) with a prompt on stdin.

The harness runs in an empty temporary directory so it does not pick up any
project's instructions, with no tools and no saved session: every call starts
from nothing, so the caller must put the whole negotiation into the prompt.
"""

import subprocess
import tempfile

MODEL = "opus"  # alias; resolved to claude-opus-5-5 on 2026-10-14
COMMAND = [
    "claude",
    "-p",
    "--model",
    MODEL,
    "--tools",
    "",
    "--setting-sources",
    "",
    "--no-session-persistence",
]
EMPTY_DIR = tempfile.mkdtemp(prefix="harness-")


class HarnessError(RuntimeError):
    """The harness failed, timed out, or returned nothing."""


def ask(prompt: str, timeout: float = 90) -> str:
    """Sends one prompt to the harness and returns its text answer.

    Args:
        prompt: The full prompt, passed on stdin.
        timeout: Seconds to wait before giving up.

    Returns:
        The harness's answer, stripped.

    Raises:
        HarnessError: On a non-zero exit, a timeout, or an empty answer.
    """
    try:
        proc = subprocess.run(
            COMMAND,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=EMPTY_DIR,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise HarnessError(f"no answer within {timeout} s") from e
    if proc.returncode != 0:
        raise HarnessError(f"exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    answer = proc.stdout.strip()
    if not answer:
        raise HarnessError("empty answer")
    return answer
