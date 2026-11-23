"""The harness call: one prompt in, one text out.

claude -p runs in an empty temp folder so it reads no project instructions
(CLAUDE.md, AGENTS.md), with its built-in tools off: the tools the agent may
use live in our own loop, not inside Claude Code.

    py model.py "say only: pong"      # one call, to check the harness works
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

MODEL = os.environ.get("W06_MODEL", "haiku")
EMPTY_DIR = tempfile.mkdtemp(prefix="w06-harness-")


def _claude():
    # The trade test may start us from a shell whose PATH predates the install.
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")
    if os.path.exists(fallback):
        return fallback
    raise RuntimeError("claude CLI not found on PATH or in ~/.local/bin")


def call_model(prompt, system=None, timeout=120):
    """Send prompt to the harness and return its text answer. Logs both to stderr."""
    cmd = [_claude(), "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
           "--no-session-persistence"]
    if system:
        cmd += ["--system-prompt", system]
    t0 = time.time()
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       cwd=EMPTY_DIR, timeout=timeout)
    out = r.stdout.strip()
    log(f"[model {time.time() - t0:.1f}s exit={r.returncode}] prompt:\n{prompt}\n[model] answer:\n{out}")
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr.strip()[:300]}")
    return out


def log(text):
    print(text, file=sys.stderr, flush=True)


if __name__ == "__main__":
    print(call_model(" ".join(sys.argv[1:]) or "say only: pong"))
