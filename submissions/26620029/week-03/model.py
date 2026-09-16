"""Model call and token meter, adapted from weeks/week-02/starter/tools_shared.py.

The contract net needs no tools: one system prompt (the contractor's skill)
and one user message (the task announcement) per bid, so this trims the
week-02 Chat class down to a single-turn call.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set   -> Anthropic SDK (pip install anthropic)
  otherwise                -> OpenAI-compatible (pip install openai)
                              OPENAI_API_KEY, optional OPENAI_BASE_URL
                              (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL               optional model override for either provider
  AGENT_TEMPERATURE          optional temperature override (default 0.7)
"""
import os

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))

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
    """Tokens and calls, same shape as week 02's Meter."""

    def __init__(self):
        self.tokens = 0
        self.iters = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.iters += 1


def call_model(system: str, user: str, meter: Meter) -> str:
    """One system+user turn, no tools, no history. Returns the reply text."""
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=300, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = _get_client().chat.completions.create(
        model=MODEL, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""
