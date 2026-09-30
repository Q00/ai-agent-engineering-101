"""Week 04 — model call and meter.

Negotiation needs no tools: call_conversation() sends a system prompt plus
the turn history so far (the other side's messages as "user", this agent's
own prior messages as "assistant") and returns the raw reply text.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI-compatible (pip install openai)
                                    OPENAI_API_KEY, optional OPENAI_BASE_URL
                                    (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL                    optional model override for either provider
"""
import os

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"

_DEFAULTS = {"anthropic": "claude-haiku-4-5", "openai": "gpt-4o-mini"}
_requested = os.environ.get("AGENT_MODEL", "").strip()
if PROVIDER == "anthropic" and _requested and not _requested.startswith("claude-"):
    print(f"model: AGENT_MODEL={_requested!r} is not an Anthropic model id; "
          f"using {_DEFAULTS['anthropic']!r} instead.")
    _requested = ""
MODEL = _requested or _DEFAULTS[PROVIDER]
TEMPERATURE = 0  # fixed for every condition

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
    """Counts tokens and model calls across a whole run."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def call_model(system: str, user: str, meter: "Meter") -> str:
    """One-shot call: system + a single user message, no history. Used by
    the reader (it only ever looks at one message at a time)."""
    return call_conversation(system, [{"role": "user", "content": user}], meter)


def call_conversation(system: str, messages: list, meter: "Meter") -> str:
    """`messages` is the turn history so far: a list of
    {"role": "user"|"assistant", "content": str}. The caller decides which
    side is "assistant" (itself) and which is "user" (the other agent)."""
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=300, temperature=TEMPERATURE,
            system=system, messages=messages)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    else:
        resp = _get_client().chat.completions.create(
            model=MODEL, temperature=TEMPERATURE,
            messages=[{"role": "system", "content": system}, *messages])
        usage = resp.usage
        meter.add(getattr(usage, "prompt_tokens", 0),
                   getattr(usage, "completion_tokens", 0))
        return resp.choices[0].message.content or ""
