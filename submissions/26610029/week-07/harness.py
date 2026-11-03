"""Shared helper: calls the non-interactive harness that drives both agents.

Every call is a fresh, non-interactive `claude -p` process (no session is kept
between turns), so the full negotiation context has to be written into the
prompt each time. The harness runs in a throwaway empty directory so it does
not pick up this repo's own CLAUDE.md/AGENTS.md as project instructions.
"""
import subprocess
import tempfile

def call_harness(prompt: str, model: str = "haiku", timeout: int = 60) -> str:
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--setting-sources", "", "--no-session-persistence"]
    with tempfile.TemporaryDirectory() as cwd:
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return proc.stdout.strip()
