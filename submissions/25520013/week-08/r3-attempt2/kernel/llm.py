"""Model call wrapper: gpt-6.1-terra through the labpc codex CLI, fixed model, no call cap (성수님 결정 2026-11-05).

One completion per call: prompt on stdin, answer on stdout, empty temp dir as cwd, codex's own tools off. The tool
loop for agents is run by the kernel, not by codex. Every call is written to the trajectory (status, latency, sizes;
never the prompt text). A subscription-limit refusal raises ModelRefused so the caller can fall back to the model's
own script and the briefing can count it.
"""

import os
import re
import stat
import subprocess
import tempfile
import time

from kernel import common as c

MODEL = "gpt-6.1-terra"
EFFORTS = ("low", "medium", "high")
ALLOWED_CLI = (
    "/home/sungsoo/.local/bin/codex",
)  # labpc, codex-cli 0.164.0 (실측 11-05)
FIXED_ARGS = (
    "--ephemeral",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--ignore-user-config",
)
TOOL_OFF = (
    "shell_tool",
    "unified_exec",
    "shell_snapshot",
    "apps",
    "plugins",
    "remote_plugin",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "view_image",
    "skill_search",
)
TOOL_OFF_ARGS = tuple(a for f in TOOL_OFF for a in ("--disable", f)) + (
    "-c",
    "shell_environment_policy.inherit=none",
    "-c",
    'web_search="disabled"',
)
QUOTA_RX = re.compile(r"(?i)rate.?limit|usage.?limit|quota|too many requests|\b429\b")
AUTH_RX = re.compile(r"(?i)unauthori[sz]ed|\b401\b|\b403\b|log ?in again|auth")


class ModelRefused(RuntimeError):
    """Subscription limit hit."""


class ModelError(RuntimeError):
    """Any other failure: timeout, auth, crash."""


def resolve_cli(cli, allowed=ALLOWED_CLI):
    if not isinstance(cli, str) or not os.path.isabs(cli):
        raise ModelError("cli must be an absolute path on the allow-list")
    real = os.path.realpath(cli)
    if real not in {os.path.realpath(a) for a in allowed}:
        raise ModelError("cli is not on the allow-list")
    try:
        s = os.stat(real)
    except OSError as e:
        raise ModelError("cli missing") from e
    if not stat.S_ISREG(s.st_mode) or not os.access(real, os.X_OK) or s.st_mode & 0o022:
        raise ModelError("cli is not a regular executable writable only by its owner")
    return real


def child_env():
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for k in ("HOME", "CODEX_HOME"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def build_cmd(exe, cwd, effort):
    if effort not in EFFORTS:
        raise ModelError(f"effort must be one of {EFFORTS}")
    return [
        exe,
        "exec",
        *FIXED_ARGS,
        "-C",
        str(cwd),
        *TOOL_OFF_ARGS,
        "-m",
        MODEL,
        "-c",
        f"model_reasoning_effort={effort}",
        "-",
    ]


class Terra:
    def __init__(
        self,
        trajectory,
        cli=ALLOWED_CLI[0],
        allowed=ALLOWED_CLI,
        runner=subprocess.run,
        secrets=(),
    ):
        self.traj, self.cli, self.allowed, self.runner = (
            trajectory,
            cli,
            tuple(allowed),
            runner,
        )
        self.secrets = tuple(s for s in secrets if s)

    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        exe = resolve_cli(self.cli, self.allowed)
        prompt = c.mask(prompt, self.secrets)
        row = {
            "model": MODEL,
            "effort": effort,
            "prompt_bytes": len(prompt.encode("utf-8")),
        }
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="r3a2-terra-") as cwd:
                out = self.runner(
                    build_cmd(exe, cwd, effort),
                    input=prompt,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout_s,
                    env=child_env(),
                )
        except subprocess.TimeoutExpired as e:
            self.traj.append(
                agent,
                "llm",
                status="timeout",
                latency_s=round(time.monotonic() - t0, 2),
                **row,
            )
            raise ModelError("timeout") from e
        lat = round(time.monotonic() - t0, 2)
        if out.returncode != 0:
            tail = " ".join(((out.stderr or "") + " " + (out.stdout or "")).split())[
                -300:
            ]
            status = (
                "quota"
                if QUOTA_RX.search(tail)
                else "auth"
                if AUTH_RX.search(tail)
                else "error"
            )
            self.traj.append(agent, "llm", status=status, latency_s=lat, **row)
            if status == "quota":
                raise ModelRefused("subscription limit")
            raise ModelError(f"codex exit {out.returncode} ({status})")
        text = (out.stdout or "").strip()
        self.traj.append(
            agent,
            "llm",
            status="ok",
            latency_s=lat,
            output_bytes=len(text.encode("utf-8")),
            **row,
        )
        return text
