"""Model backend for the week-03 contract net.

One bid is one model call: one system prompt, one user message, no tools.

Two providers, picked from the environment. With `OPENAI_BASE_URL` and
`OPENAI_API_KEY` set, calls go to that OpenAI-compatible endpoint — the
README's OpenRouter path, which `extra/` uses. Otherwise they go to `claude -p`,
which is what stage 1 ran on (a Claude subscription, cleared with the
instructor). Nothing else about the protocol changes either way.

`claude -p` exposes neither a temperature nor a seed (checked on CLI 2.1.272),
so the runs are not bit-reproducible. What is pinned instead, and stated in
REPORT.md, is the dated model id, the flag set below, and the exclusion of the
CLI's dynamic system-prompt sections so the wrapper text does not drift by day.
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
TIMEOUT_S = 120

# A free endpoint rate-limits, and a run is a few hundred calls. Retries are
# bounded and counted; a call that still fails is a contractor that did not
# answer, which the protocol already has a meaning for.
RETRIES = 3
BACKOFF_S = 8

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

    Returns the empty string when the provider errors or times out. The caller
    treats that exactly like an unparseable reply: a contractor that did not
    bid, counted and reported.
    """
    if BASE_URL and os.environ.get("OPENAI_API_KEY"):
        return _http(system, user, meter)
    return _cli(system, user, meter)


def _http(system: str, user: str, meter: Meter) -> str:
    """One chat completion against an OpenAI-compatible endpoint."""
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as reply:
                payload = json.loads(reply.read())
            break
        except urllib.error.HTTPError as err:
            if err.code in (429, 502, 503) and attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            meter.fail()
            return ""
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            meter.fail()
            return ""
    usage = payload.get("usage") or {}
    meter.add(usage.get("prompt_tokens"), usage.get("completion_tokens"))
    choices = payload.get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""


def _cli(system: str, user: str, meter: Meter) -> str:
    """One `claude -p` call, tools denied."""
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
