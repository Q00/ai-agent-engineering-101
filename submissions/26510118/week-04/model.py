"""Week 04 -- the model calls the negotiation needs.

Adapted from week 03's model.py. Three changes, all forced by this lab:

  1. A negotiation is multi-turn, so call_model takes a message list instead of
     one user string. The other side's message is a `user` turn, the agent's own
     message an `assistant` turn.
  2. Free endpoints return HTTP 429 in bursts, so calls retry with a growing
     wait instead of killing the episode.
  3. reader_calls is a measured variable, so agent calls and reader calls are
     counted in separate Meters. The caller decides which Meter to pass.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set  -> Anthropic SDK   (pip install anthropic)
  otherwise              -> OpenAI-compatible (pip install openai)
                            OPENAI_API_KEY, optional OPENAI_BASE_URL
  AGENT_MODEL            optional model override
  AGENT_TEMPERATURE      optional; default below
"""
import os
import time


class Meter:
    """What a run costs. Counted in one place so every condition is measured alike.

    One Meter per kind of call: the agents share one, the reader gets its own,
    so reader_calls is just reader_meter.calls.
    """

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.retries = 0        # 429s and other transients we waited out

    def add(self, n_in, n_out):
        self.tokens += int(n_in or 0) + int(n_out or 0)
        self.calls += 1


PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))

# Reasoning models on OpenRouter put their thinking into the message text unless
# it is switched off. Sent only when a custom base URL is set: api.openai.com
# rejects unknown body fields with a 400.
EXTRA_BODY = ({"reasoning": {"enabled": False}}
              if os.environ.get("OPENAI_BASE_URL") else None)

# Growing wait, in seconds. Five attempts total; after the last one the error
# propagates and run.py records the episode as crashed rather than silently
# dropping it.
RETRY_WAITS = (2, 5, 12, 30, 60)

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


def _is_transient(e: Exception) -> bool:
    """429 and friends: worth waiting out. A 400 or a 401 is not."""
    code = getattr(e, "status_code", None)
    if code is None:
        code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (408, 409, 429, 500, 502, 503, 504):
        return True
    return type(e).__name__ in (
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "OverloadedError", "ServiceUnavailableError")


def call_model(system: str, messages: list, meter: Meter, log=None) -> str:
    """One call: a system prompt and the conversation so far, the reply text back.

    `messages` is a list of {"role": "user"|"assistant", "content": str}. It must
    be non-empty and start with a user turn -- Anthropic rejects anything else,
    and keeping the same shape for both providers means a run reproduces across
    them. The agent that speaks first is seeded with an opener by negotiate.py.
    """
    client = _get_client()

    for attempt, wait in enumerate((*RETRY_WAITS, None)):
        try:
            if PROVIDER == "anthropic":
                resp = client.messages.create(
                    model=MODEL, max_tokens=512, temperature=TEMPERATURE,
                    system=system, messages=messages)
                meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
                return "".join(b.text for b in resp.content if b.type == "text")

            kwargs = {}
            if EXTRA_BODY is not None:
                kwargs["extra_body"] = EXTRA_BODY
            resp = client.chat.completions.create(
                model=MODEL, temperature=TEMPERATURE,
                messages=[{"role": "system", "content": system}] + list(messages),
                **kwargs)
            usage = resp.usage
            meter.add(getattr(usage, "prompt_tokens", 0),
                      getattr(usage, "completion_tokens", 0))
            return resp.choices[0].message.content or ""

        except Exception as e:                      # noqa: BLE001
            if wait is None or not _is_transient(e):
                raise
            meter.retries += 1
            if log:
                log(f"    [retry {attempt + 1}] {type(e).__name__}: waiting {wait}s")
            time.sleep(wait)
