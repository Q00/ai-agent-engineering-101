"""Model access for the week-03 contract net.

Trimmed from weeks/week-02/starter/tools_shared.py (the course starter). The
contract net needs no tools — only a system prompt and one user message per bid
— so this keeps just the provider selection, the Meter, and a single-shot
completion. Temperature is fixed for every call so the three conditions differ
*only* in the contractor system prompts, nothing else.

Provider (same rule as weeks 01-02):
  ANTHROPIC_API_KEY set  -> Anthropic SDK
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, optional OPENAI_BASE_URL)
  AGENT_MODEL            -> optional model override
  AGENT_TEMPERATURE      -> optional; default 0 (hold constant across conditions)
"""
import os

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))

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
    """Token and call accounting in one place (from the starter's Meter)."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0          # one call = one model round-trip = one bid

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def complete(system: str, user: str, meter: Meter) -> str:
    """One model round-trip: system prompt + one user message -> assistant text.

    No tools. Temperature is fixed (TEMPERATURE) so a change in behaviour
    between conditions can only come from the system prompt.
    """
    client = _get_client()
    if PROVIDER == "anthropic":
        resp = client.messages.create(
            model=MODEL, max_tokens=1024, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    resp = client.chat.completions.create(
        model=MODEL, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    u = resp.usage
    meter.add(getattr(u, "prompt_tokens", 0) if u else 0,
              getattr(u, "completion_tokens", 0) if u else 0)
    # A free provider sometimes returns 200 with no choices (rate limit / error
    # payload). Treat that as an empty reply -> the caller records a no-bid.
    choices = resp.choices or []
    if not choices:
        return ""
    return choices[0].message.content or ""
