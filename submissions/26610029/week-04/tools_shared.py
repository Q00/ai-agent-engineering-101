"""Week 04 — model call and meter shared by the buyer/seller/reader.

No tools this week (README: "No tools are needed: one system prompt per
agent, and the other agent's messages as the conversation"). Anthropic only
(this submission's provider choice), temperature/top_p exposed since the
experiment fixes them explicitly (temperature=1.0, top_p=0.95).

Requires: pip install anthropic, and in the environment:
  ANTHROPIC_API_KEY   your key
  AGENT_MODEL         optional; defaults to claude-sonnet-4-5
"""
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv():
    """Tiny .env loader (no extra dependency): .env always wins over a stale
    shell export, so switching AGENT_MODEL only means editing this file.
    .env is git-ignored (repo-wide rule) — never commit it."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


_load_dotenv()

TEMPERATURE = 1.0  # Anthropic default; top_p is not used (API rejects setting both)
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def create_message(**kwargs):
    """messages.create() with a growing wait on HTTP 429 (README: 'Retry with
    a growing wait instead of failing the episode'). 5 tries: 2s,4s,8s,16s."""
    import anthropic
    wait = 2
    for attempt in range(5):
        try:
            return _get_client().messages.create(**kwargs)
        except anthropic.RateLimitError:
            if attempt == 4:
                raise
            time.sleep(wait)
            wait *= 2


class Meter:
    """Token/iteration counter, same shape as week 02/03."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


@dataclass
class Reply:
    text: str


class Chat:
    """One conversation with the model. No tools, no reader-specific logic —
    buyer, seller, and the free-condition reader all just exchange plain
    messages through this class."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.messages = []

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str):
        self.messages.append({"role": "assistant", "content": text})

    def send(self) -> Reply:
        resp = create_message(
            model=MODEL, max_tokens=512, system=self.system, messages=self.messages,
            extra_body={"temperature": TEMPERATURE})
        self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        text = "".join(b.text for b in resp.content if b.type == "text")
        self.messages.append({"role": "assistant", "content": text})
        return Reply(text)
