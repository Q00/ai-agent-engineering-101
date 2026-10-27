"""Calls the harness (Codex, non-interactive) with a prompt on stdin.

The harness runs in an empty temporary directory so it does not pick up any
project's instructions, with no tools and no saved session: every call starts
from nothing, so the caller must put the whole negotiation into the prompt.
"""

import os
import subprocess
import tempfile

# Shared model for the buyer, scouting, and review feedback.
MODEL = os.environ.get("LSS_HARNESS_MODEL", "gpt-6.1-terra")
COMMAND = [
    "codex",
    "exec",
    "--model",
    MODEL,
    "-c",
    'model_reasoning_effort="medium"',
    "--sandbox",
    "read-only",
    "--skip-git-repo-check",
    "--ephemeral",
    "--ignore-user-config",
    "--disable",
    "shell_tool",
    "--disable",
    "apply_patch_freeform",
    "--disable",
    "multi_agent",
    "-c",
    'web_search="disabled"',
    "-",
]
EMPTY_DIR = tempfile.mkdtemp(prefix="harness-")


class HarnessError(RuntimeError):
    """The harness failed, timed out, or returned nothing."""


def ask(prompt: str, timeout: float = 90, model: str | None = None) -> str:
    """Sends one prompt to the harness and returns its text answer.

    Args:
        prompt: The full prompt, passed on stdin.
        timeout: Seconds to wait before giving up.
        model: Model alias for this call; None means MODEL. The seller's negotiation
            turns can run a cheaper model than the scout and the review feedback.

    Returns:
        The harness's answer, stripped.

    Raises:
        HarnessError: On a non-zero exit, a timeout, or an empty answer.
    """
    command = list(COMMAND)
    if model is not None:
        command[command.index("--model") + 1] = model
    try:
        proc = subprocess.run(
            command,
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
