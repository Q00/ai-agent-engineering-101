"""Model access for the week-04 negotiation lab.

Generalised from the week-03 `llm.py` (which itself trimmed the week-02 starter
`tools_shared.py`). A negotiation turn is a multi-message conversation, so
`call_model` takes a message list instead of one user string. The reader uses
the same function with a single user message. No tools. Temperature is fixed
for every call — agents and reader alike — so only the format changes.

Provider:
  ANTHROPIC_API_KEY set  -> Anthropic SDK
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, optional OPENAI_BASE_URL)
  AGENT_MODEL            -> optional model override
  AGENT_TEMPERATURE      -> optional; default 0
  AGENT_NO_REASONING     -> "1" to send extra_body disabling reasoning (free
                           OpenRouter reasoning models put thinking in the text)
"""
import os

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
NO_REASONING = os.environ.get("AGENT_NO_REASONING") == "1"

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Meter:
    """Token and call accounting in one place."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def call_model(system, messages, meter):
    """system prompt + a list of {role, content} messages -> assistant text.

    `messages` uses roles "user"/"assistant" with string content, which both
    providers accept. Empty/failed responses return "" (the caller treats an
    unreadable message as a format error, not a crash).
    """
    client = _get_client()
    if PROVIDER == "anthropic":
        resp = client.messages.create(
            model=MODEL, max_tokens=512, temperature=TEMPERATURE,
            system=system, messages=messages)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    kwargs = dict(model=MODEL, temperature=TEMPERATURE,
                  messages=[{"role": "system", "content": system}, *messages])
    if NO_REASONING:
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    resp = client.chat.completions.create(**kwargs)
    u = resp.usage
    meter.add(getattr(u, "prompt_tokens", 0) if u else 0,
              getattr(u, "completion_tokens", 0) if u else 0)
    choices = resp.choices or []
    return choices[0].message.content or "" if choices else ""
