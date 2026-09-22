"""Week 04 — model call and meter, adapted from week-03's model.py.

Negotiation needs no tools, but unlike week 03's one-shot bid, each side must
see the turns so far, so call_conversation() takes a message history instead
of a single user string (call_model() is kept for any one-shot use, e.g. the
free/tagged reader).
Provider is picked from the environment, same as week 02:
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
TEMPERATURE = 0  # fixed for every condition; see run.py header comment

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
    """One model call, system + one user message, no history, no tools.
    Returns the raw text reply (the contractor's bid, still unparsed)."""
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=300, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    else:
        # Anthropic accepts temperature=0 directly; the OpenAI-compatible
        # path is used for OpenRouter free models too, same parameter name.
        resp = _get_client().chat.completions.create(
            model=MODEL, temperature=TEMPERATURE,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}])
        usage = resp.usage
        meter.add(getattr(usage, "prompt_tokens", 0),
                   getattr(usage, "completion_tokens", 0))
        return resp.choices[0].message.content or ""


def call_conversation(system: str, messages: list, meter: "Meter") -> str:
    """Like call_model, but `messages` is the full turn history so far:
    a list of {"role": "user"|"assistant", "content": str}. Each side calls
    this with its own system prompt and the same shared history, so it
    always answers as if it just received the other side's last message."""
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
