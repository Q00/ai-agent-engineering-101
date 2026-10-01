"""Week 03 — the one model call the contract net needs.

Adapted from weeks/week-02/starter/tools_shared.py. The contract net uses no
tools: one system prompt and one user message per bid, one text reply back.
The caller parses that text; this module never looks inside it.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set  -> Anthropic SDK   (pip install anthropic)
  otherwise              -> OpenAI-compatible (pip install openai)
                            OPENAI_API_KEY, optional OPENAI_BASE_URL
  AGENT_MODEL            optional model override
  AGENT_TEMPERATURE      optional; default below
"""
import os


class Meter:
    """What a run costs. Counted in one place so every condition is measured alike."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, n_in, n_out):
        self.tokens += int(n_in or 0) + int(n_out or 0)
        self.calls += 1


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


def settings_line() -> str:
    """First line of every log file, so a run can be reproduced from the log alone."""
    return f"provider={PROVIDER} model={MODEL} temperature={TEMPERATURE}"


def call_model(system: str, user: str, meter: Meter) -> str:
    """One call: one system prompt, one user message, the reply text back."""
    client = _get_client()

    if PROVIDER == "anthropic":
        resp = client.messages.create(
            model=MODEL, max_tokens=512, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = client.chat.completions.create(
        model=MODEL, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0),
              getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""
