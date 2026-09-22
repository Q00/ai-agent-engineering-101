"""Week 03 — the model call, trimmed down from week 02's tools_shared.py.

The contract net needs no tools: a contractor gets a system prompt and one
user message (the announcement) and answers with a JSON bid. So Chat here is
the week-02 Chat with the tool plumbing removed and two things added that the
free-tier runs of week 02 made necessary:

  * temperature is pinned and recorded, because the three conditions have to
    differ only in the prompts;
  * a bounded retry around the rate limits that crashed six of week 02's runs.
    A retry is transport, not protocol: it is logged but never counted as a
    contract-net message.

Provider is picked from the environment, same as week 02:
  ANTHROPIC_API_KEY set  -> Anthropic SDK
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL            -> model override
  AGENT_TEMPERATURE      -> temperature override (default 0.0)
  AGENT_PACE_SECONDS     -> sleep between calls, for free-tier per-minute caps
"""
import os
import time

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.0"))
PACE_SECONDS = float(os.environ.get("AGENT_PACE_SECONDS", "3.5"))
MAX_ATTEMPTS = int(os.environ.get("AGENT_MAX_ATTEMPTS", "4"))

_client = None
_last_call = [0.0]


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
    """Tokens and model calls, counted in one place. Contract-net messages are
    counted by the manager, not here: they are different units."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.retries = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


class Chat:
    """One stateless ask: system prompt in, text out. Contractors do not carry
    a conversation between tasks -- each announcement is judged on its own,
    which is what makes the bids comparable across tasks."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter

    def ask(self, user: str, log=print, max_tokens: int = 400) -> str:
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            wait = PACE_SECONDS - (time.time() - _last_call[0])
            if wait > 0:
                time.sleep(wait)
            try:
                text = self._send(user, max_tokens)
                _last_call[0] = time.time()
                return text
            except Exception as e:                  # rate limits and upstream 5xx
                _last_call[0] = time.time()
                last_error = e
                self.meter.retries += 1
                if attempt == MAX_ATTEMPTS:
                    break
                backoff = 5 * attempt
                log(f"    [retry {attempt}/{MAX_ATTEMPTS - 1}] "
                    f"{type(e).__name__}: {str(e)[:160]} -- sleeping {backoff}s")
                time.sleep(backoff)
        raise last_error

    def _send(self, user: str, max_tokens: int) -> str:
        if PROVIDER == "anthropic":
            resp = _get_client().messages.create(
                model=MODEL, max_tokens=max_tokens, temperature=TEMPERATURE,
                system=self.system, messages=[{"role": "user", "content": user}])
            self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
            return "".join(b.text for b in resp.content if b.type == "text")
        resp = _get_client().chat.completions.create(
            model=MODEL, temperature=TEMPERATURE, max_tokens=max_tokens,
            messages=[{"role": "system", "content": self.system},
                      {"role": "user", "content": user}])
        usage = resp.usage
        self.meter.add(getattr(usage, "prompt_tokens", 0),
                       getattr(usage, "completion_tokens", 0))
        return resp.choices[0].message.content or ""
