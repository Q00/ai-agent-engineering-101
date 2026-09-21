"""One model call, one place. Copied from week-02 starter `tools_shared.py`
(Chat/Meter) and cut down: the contract net needs no tools, only a system
prompt and one user message per bid.

Two things were added for week 03, both because they are controlled variables
that the report has to state:

  * `temperature` and `max_tokens` are passed explicitly instead of taking the
    provider default. Week 02 never set them, so its runs were not repeatable
    in that respect.
  * the model call is injectable (`manager` and `bond_net` take an `ask`
    callable), so the metric counting can be verified without a provider.

Environment:
  OPENAI_API_KEY   required for live runs; never read from a file, never logged
  OPENAI_BASE_URL  leave unset for OpenAI itself; set it to point elsewhere
  AGENT_MODEL      default gpt-4o-mini
  AGENT_TEMPERATURE default 0
  AGENT_MAX_TOKENS default 300
"""
import os

MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "300"))
BASE_URL = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"

_client = None
# set once a provider has refused `temperature`; the report then has to say
# "not settable" instead of quoting a number we never actually sent
temperature_rejected = False


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()          # reads OPENAI_API_KEY / OPENAI_BASE_URL
    return _client


class Meter:
    """Tokens and model calls, counted in one place. `messages` is counted by
    the manager instead: a protocol message is not a model call."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def settings_line() -> str:
    """First line of every log file."""
    temp = "not settable (provider refused it)" if temperature_rejected else TEMPERATURE
    return (f"provider=openai base_url={BASE_URL} model={MODEL} "
            f"temperature={temp} max_tokens={MAX_TOKENS}")


def ask(system: str, user: str, meter: Meter) -> str:
    """One system prompt, one user message, one reply. Returns raw text."""
    global temperature_rejected
    kwargs = dict(model=MODEL,
                  messages=[{"role": "system", "content": system},
                            {"role": "user", "content": user}],
                  max_tokens=MAX_TOKENS,
                  temperature=TEMPERATURE)
    try:
        resp = _get_client().chat.completions.create(**kwargs)
    except Exception as e:
        # reasoning models reject a non-default temperature; retry once without
        # it and record that the control variable was not settable
        if "temperature" not in str(e):
            raise
        temperature_rejected = True
        kwargs.pop("temperature")
        resp = _get_client().chat.completions.create(**kwargs)
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""
