"""Model call and meter, copied from the week-02 starter and trimmed.

The contract net needs no tools: one system prompt and one user message per
bid. What is kept from week 02: the provider switch, the Meter, and the
transport retry for the free tier's empty-choices 502. What is added: a fixed
temperature and max_tokens (control variables of the experiment) and the
model name the provider reports back, so the log can show which model
actually answered.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI-compatible (pip install openai)
                                    OPENAI_API_KEY, optional OPENAI_BASE_URL
                                    (https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL                    optional model override for either provider
"""
import os
import time

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")

# Control variables. Same for every contractor, condition and run.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "512"))
TIMEOUT_S = float(os.environ.get("AGENT_TIMEOUT", "60"))
CALL_DELAY_S = float(os.environ.get("AGENT_CALL_DELAY", "0"))   # pause before each call (per-minute limits)


class Meter:
    """Tokens and calls, counted in one place."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.model_reported = None   # model name from the first response

    def add(self, input_tokens, output_tokens, model=None):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1
        if model and not self.model_reported:
            self.model_reported = model


_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic(timeout=TIMEOUT_S)
        else:
            from openai import OpenAI
            _client = OpenAI(timeout=TIMEOUT_S)
    return _client


# ---------------------------------------------------------------- transport

# Free-tier OpenRouter answers a transient upstream outage with
# {"choices": null, "error": {"code": 502, ...}}. The SDK turns that into a
# response whose .choices is None. Retried identically for every contractor,
# so it cannot bias a condition. Each attempt is still one request against
# the daily free-model quota.
TRANSPORT_RETRIES = int(os.environ.get("AGENT_TRANSPORT_RETRIES", "3"))


# A 429 can be a per-minute limit (clears in seconds) or a daily quota
# (does not). Wait 10s, 20s, 40s and retry; if it still fails, let the
# RateLimitError propagate, which the runner records as a crashed run.
RATE_LIMIT_RETRIES = int(os.environ.get("AGENT_RATE_LIMIT_RETRIES", "3"))


def _create_with_retry(create, kwargs):
    from openai import RateLimitError
    last = "empty choices"
    rate_hits = 0
    attempt = 0
    while attempt < TRANSPORT_RETRIES:
        if CALL_DELAY_S:
            time.sleep(CALL_DELAY_S)
        try:
            resp = create(**kwargs)
        except RateLimitError:
            rate_hits += 1
            if rate_hits > RATE_LIMIT_RETRIES:
                raise
            time.sleep(10 * 2 ** (rate_hits - 1))
            continue                                  # does not use up a transport attempt
        if getattr(resp, "choices", None):
            return resp
        last = getattr(resp, "error", None) or last
        time.sleep(2 ** attempt)
        attempt += 1
    raise RuntimeError(
        f"provider returned no choices after {TRANSPORT_RETRIES} attempts: {last}")


class Chat:
    """One conversation with the model: a system prompt and one user message
    per bid. A fresh Chat per announcement keeps one bid from leaking into
    the next (context policy `fresh`)."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.messages = []
        if PROVIDER == "openai":
            self.messages.append({"role": "system", "content": system})

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def send(self) -> str:
        if PROVIDER == "anthropic":
            return self._send_anthropic()
        return self._send_openai()

    def _send_anthropic(self) -> str:
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            system=self.system, messages=self.messages)
        self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens, resp.model)
        return "".join(b.text for b in resp.content if b.type == "text")

    def _send_openai(self) -> str:
        kwargs = dict(model=MODEL, messages=self.messages,
                      temperature=TEMPERATURE, max_tokens=MAX_TOKENS)
        resp = _create_with_retry(_get_client().chat.completions.create, kwargs)
        usage = resp.usage
        self.meter.add(getattr(usage, "prompt_tokens", 0),
                       getattr(usage, "completion_tokens", 0),
                       getattr(resp, "model", None))
        return resp.choices[0].message.content or ""
