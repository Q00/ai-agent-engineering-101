"""Non-interactive harness adapter shared by the buyer and seller."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass


DEFAULT_COMMAND = (
    "/usr/bin/env SSL_CERT_FILE=/etc/ssl/cert.pem "
    "codex exec --ignore-user-config --ephemeral "
    "--skip-git-repo-check -m gpt-6-luna -"
)


class HarnessResponseError(ValueError):
    """Raised when a harness answer does not follow the decision contract."""


@dataclass(frozen=True)
class Decision:
    """One structured negotiation decision returned by a harness."""

    action: str
    price: int | None = None
    reason: str = ""


def parse_decision(text: str) -> Decision:
    """Parse the first JSON object in a harness response."""
    match = re.search(r"\{.*?\}", text, flags=re.DOTALL)
    if match is None:
        raise HarnessResponseError("harness response contains no JSON object")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError as error:
        raise HarnessResponseError("harness response contains invalid JSON") from error
    action = value.get("action")
    if not isinstance(action, str) or not action.strip():
        raise HarnessResponseError("decision.action must be a non-empty string")
    price = value.get("price")
    if price is not None:
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise HarnessResponseError("decision.price must be numeric or null")
        price = round(price)
    reason = value.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)
    return Decision(action.strip().lower(), price, reason.strip())


class CommandHarness:
    """Call a stdin/stdout harness in an isolated empty working directory."""

    def __init__(self, command: str | None = None, timeout: int = 90) -> None:
        self.command = command or os.environ.get("WEEK06_HARNESS_CMD", DEFAULT_COMMAND)
        self.timeout = timeout

    def decide(self, prompt: str, fallback: Decision) -> Decision:
        """Return a structured decision, using a logged safe fallback on failure."""
        try:
            with tempfile.TemporaryDirectory(prefix="week06-harness-") as workdir:
                completed = subprocess.run(
                    shlex.split(self.command),
                    input=prompt,
                    text=True,
                    capture_output=True,
                    cwd=workdir,
                    timeout=self.timeout,
                    check=False,
                )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()[-1:] or ["no stderr"]
                raise RuntimeError(
                    f"harness exited {completed.returncode}: {detail[0][:200]}"
                )
            decision = parse_decision(completed.stdout)
            self._log("decision", decision=decision)
            return decision
        except (OSError, RuntimeError, subprocess.TimeoutExpired, HarnessResponseError) as error:
            self._log("fallback", decision=fallback, error=f"{type(error).__name__}: {error}")
            return fallback

    @staticmethod
    def _log(event: str, decision: Decision, error: str = "") -> None:
        record = {
            "event": f"harness_{event}",
            "action": decision.action,
            "price": decision.price,
            "reason": decision.reason,
        }
        if error:
            record["error"] = error
        print(json.dumps(record, ensure_ascii=False), file=sys.stderr, flush=True)

