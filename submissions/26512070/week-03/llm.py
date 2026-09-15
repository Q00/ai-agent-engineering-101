"""One stateless model call, plus the JSON salvage the free models make necessary.

Adapted from weeks/week-02/starter/tools_shared.py. Two deliberate changes:

  * Tools are gone. The contract net negotiates in text; nobody calls a tool.
  * Temperature is explicit and read from the environment, because the three
    conditions are only comparable if every one of them ran at the same value.

Every agent here is stateless per call. The one piece of memory in the whole
system is the Bias agent's hypothesis list, and it carries that in its own
prompt text -- so the memory is visible in the log instead of hiding in an
object. See bias.py.
"""
import json
import os
import re

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = 900

_client = None


class Meter:
    """Token and call cost. Negotiation cost in messages is counted by the
    manager, not here -- these are two different notions of 'what it cost'."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


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


def ask(system: str, user: str, meter: Meter) -> str:
    """One turn: a system prompt and a single user message. Returns raw text."""
    client = _get_client()
    if PROVIDER == "anthropic":
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = client.chat.completions.create(
        model=MODEL, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


def extract_json(text: str):
    """Pull the first balanced JSON object out of a reply, or return None.

    The free models on OpenRouter often answer with their reasoning and bury
    the object in it, or fence it. None is not an error to hide: the manager
    counts it as a contractor that failed to bid, which is a result worth
    reporting, not a bug worth retrying away.
    """
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    start = body.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(body[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def clamp_int(value, low=0, high=100, default=0) -> int:
    """A model that answers "약 80%" or 8.5 or "high" should not crash the run."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        n = int(value)
    elif isinstance(value, str):
        m = re.search(r"-?\d+", value)
        if not m:
            return default
        n = int(m.group())
    else:
        return default
    return max(low, min(high, n))
