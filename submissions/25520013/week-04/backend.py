"""Model backend for week-04: one conversation per agent, two providers.

A negotiation is multi-turn, so each agent needs a conversation of its own in
which its own messages are assistant turns and the other side's are user turns.
`Session` holds one such conversation. The reader is not a participant in the
negotiation, so it gets no session: `ask` is a single stateless call.

Two providers, picked from the environment. With `OPENAI_BASE_URL` and
`OPENAI_API_KEY` set, calls go to that OpenAI-compatible endpoint, which is the
README's OpenRouter path and the one to reproduce these runs with. Otherwise
they go to `claude -p`, a Claude subscription cleared with the instructor,
which is what the committed runs used. The protocol layer is unaware of either.

`claude -p` exposes neither a temperature nor a seed (checked on CLI 2.1.278),
so those runs are not bit-reproducible and REPORT.md records temperature as not
settable. What is pinned instead is the dated model id, the flag set below, the
exclusion of the CLI's dynamic system-prompt sections, and a scratch working
directory, so no repository CLAUDE.md or local setting reaches an agent.
"""

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
# The reader is the measuring instrument, so a run that swaps the negotiators'
# model keeps it on the default unless told otherwise: a change in the results
# then belongs to the agents, not to a different observer.
READER_MODEL = os.environ.get("READER_MODEL", DEFAULT_MODEL)
BASE_URL = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
TIMEOUT_S = 180

# A free endpoint rate-limits and a full run is a few hundred calls. Retries are
# bounded and counted; a call that still fails ends the episode as a crash,
# which the runner records with a blank outcome and the reason in `note`.
RETRIES = 3
BACKOFF_S = 8

# Two negotiators and an observer need no tools. Denying them keeps one turn at
# one model call and keeps the message count honest.
_NO_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
    "NotebookEdit",
]

# The CLI reads instruction files from its working directory. Running it from an
# empty one keeps this repository's CLAUDE.md out of an agent's context.
_SCRATCH = tempfile.mkdtemp(prefix="week04-cli-")


def _http_available() -> bool:
    return bool(BASE_URL and os.environ.get("OPENAI_API_KEY"))


def provider() -> str:
    """Name of the provider in use, for the first line of a log."""
    return f"openai-compatible {BASE_URL}" if _http_available() else "claude-cli"


class Meter:
    """Call, token, and failure counts, split by who spent them."""

    def __init__(self):
        self.agent_calls = 0
        self.reader_calls = 0
        self.tokens = 0
        self.failures = 0

    def add(self, input_tokens, output_tokens, *, reader: bool):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        if reader:
            self.reader_calls += 1
        else:
            self.agent_calls += 1

    def fail(self):
        self.failures += 1


class BackendError(RuntimeError):
    """The provider did not answer after the bounded retries."""


class Session:
    """One agent's conversation.

    The agent's own replies are assistant turns and the other agent's messages
    are user turns. Over the CLI that ordering is the session itself: the first
    call carries the system prompt and returns a session id, and every later
    call resumes it. Over HTTP the message list below is that same history.
    """

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.session_id = None
        self.messages = [{"role": "system", "content": system}]

    def send(self, user: str) -> str:
        """Add one user turn, return the assistant reply, keep both in history."""
        if _http_available():
            self.messages.append({"role": "user", "content": user})
            text = _http(self.messages, self.meter, reader=False, model=MODEL)
            self.messages.append({"role": "assistant", "content": text})
            return text
        payload = _cli(
            user,
            self.meter,
            system=self.system,
            session_id=self.session_id,
            reader=False,
            model=MODEL,
        )
        self.session_id = payload.get("session_id") or self.session_id
        return payload.get("result", "")


def ask(system: str, user: str, meter: Meter) -> str:
    """One stateless call: one system prompt, one user message, one reply.

    This is the reader. It keeps no history, so labelling one message never
    depends on how many messages it has labelled before.
    """
    if _http_available():
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return _http(messages, meter, reader=True, model=READER_MODEL)
    return _cli(
        user, meter, system=system, session_id=None, reader=True, model=READER_MODEL
    ).get("result", "")


def _http(messages: list, meter: Meter, *, reader: bool, model: str) -> str:
    """One chat completion against an OpenAI-compatible endpoint."""
    body = json.dumps({"model": model, "messages": messages}).encode()
    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        },
    )
    for attempt in range(RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as reply:
                payload = json.loads(reply.read())
            break
        except urllib.error.HTTPError as err:
            meter.fail()
            if err.code in (429, 500, 502, 503) and attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            raise BackendError(f"HTTP {err.code}") from err
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
            meter.fail()
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF_S * (attempt + 1))
                continue
            raise BackendError(str(err)) from err
    usage = payload.get("usage") or {}
    meter.add(usage.get("prompt_tokens"), usage.get("completion_tokens"), reader=reader)
    choices = payload.get("choices") or [{}]
    return (choices[0].get("message") or {}).get("content") or ""


def _cli(
    user: str, meter: Meter, *, system: str, session_id, reader: bool, model: str
) -> dict:
    """One `claude -p` call, either opening a session or resuming one."""
    if session_id:
        cmd = [
            "claude",
            "--print",
            "--resume",
            session_id,
            "--output-format",
            "json",
            "--disallowed-tools",
            *_NO_TOOLS,
            "--",
            user,
        ]
    else:
        cmd = [
            "claude",
            "--print",
            "--model",
            model,
            "--output-format",
            "json",
            "--exclude-dynamic-system-prompt-sections",
            "--setting-sources",
            "",
            "--system-prompt",
            system,
            "--disallowed-tools",
            *_NO_TOOLS,
            "--",
            user,
        ]
    for attempt in range(RETRIES):
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TIMEOUT_S, cwd=_SCRATCH
            )
            payload = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (subprocess.TimeoutExpired, json.JSONDecodeError):
            payload = None
        if payload is not None:
            usage = payload.get("usage") or {}
            meter.add(
                usage.get("input_tokens"), usage.get("output_tokens"), reader=reader
            )
            return payload
        meter.fail()
        if attempt < RETRIES - 1:
            time.sleep(BACKOFF_S * (attempt + 1))
    raise BackendError(f"claude -p failed {RETRIES} times")
