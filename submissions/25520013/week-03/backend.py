"""Model backend for the week-03 contract net.

One bid is one `claude -p` call: one system prompt, one user message, no tools.
The CLI replaces the OpenRouter endpoint of the README because this submission
runs on a Claude subscription (cleared with the instructor); nothing else about
the protocol changes.

`claude -p` exposes neither a temperature nor a seed (checked on CLI 2.1.272),
so the runs are not bit-reproducible. What is pinned instead, and stated in
REPORT.md, is the dated model id, the flag set below, and the exclusion of the
CLI's dynamic system-prompt sections so the wrapper text does not drift by day.
"""
import json
import os
import subprocess
import threading

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT_S = 120

# The contract net needs no tools. Denying them keeps one bid at one model call
# and keeps the message count honest.
_NO_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch",
             "WebSearch", "Task", "TodoWrite", "NotebookEdit"]


class Meter:
    """Token, call, and CLI-failure counts, in one place (week-02 `Meter`)."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.failures = 0
        self._lock = threading.Lock()   # bids are issued from a thread pool

    def add(self, input_tokens, output_tokens):
        with self._lock:
            self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
            self.calls += 1

    def fail(self):
        with self._lock:
            self.failures += 1
            self.calls += 1


def ask(system: str, user: str, meter: Meter) -> str:
    """Send one system prompt and one user message; return the reply text.

    Returns the empty string when the CLI errors or times out. The caller
    treats that exactly like an unparseable reply: a contractor that did not
    bid, counted and reported.
    """
    cmd = ["claude", "--print",
           "--model", MODEL,
           "--output-format", "json",
           "--exclude-dynamic-system-prompt-sections",
           "--setting-sources", "",
           "--system-prompt", system,
           "--disallowed-tools", *_NO_TOOLS,
           "--", user]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        meter.fail()
        return ""
    if proc.returncode != 0:
        meter.fail()
        return ""
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        meter.fail()
        return ""
    usage = payload.get("usage", {})
    meter.add(usage.get("input_tokens"), usage.get("output_tokens"))
    return payload.get("result", "")
