"""Week 04 — model call and meter, adapted from week-03's llm_chat.py.

Same Chat/Meter shape and the same two providers as week 03: this
assignment runs through a real API, not a CLI wrapper.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  otherwise                      -> OpenAI SDK (pip install openai), plain OpenAI
                                    by default; set OPENAI_BASE_URL to point at
                                    an OpenAI-compatible provider (e.g. OpenRouter)
  AGENT_MODEL                    optional model override for either provider
                                    (default here: gpt-5-mini)
  AGENT_TEMPERATURE              optional float; ignored for reasoning models
                                    (gpt-5*, o1*, o3*, o4*), which only support
                                    their fixed default temperature
"""
import os
from dataclasses import dataclass

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "gpt-5-mini" if PROVIDER == "openai" else "claude-sonnet-4-5")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.7"))

# reasoning models (gpt-5 family, o1, o3, o4) reject a custom temperature and
# only take the API's default; skip the parameter entirely for them instead
# of guessing which value they'll accept.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
IS_REASONING_MODEL = PROVIDER == "openai" and MODEL.startswith(_REASONING_PREFIXES)

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
    """Tokens and model-call count, same idea as weeks 02-03's Meter."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


@dataclass
class Reply:
    text: str


class Chat:
    """One conversation with the model: a system prompt plus user turns."""

    def __init__(self, system: str, meter: Meter):
        self.system = system
        self.meter = meter
        self.messages = []
        if PROVIDER == "openai":
            self.messages.append({"role": "system", "content": system})

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def send(self) -> Reply:
        if PROVIDER == "anthropic":
            return self._send_anthropic()
        return self._send_openai()

    def _send_anthropic(self) -> Reply:
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=300, temperature=TEMPERATURE,
            system=self.system, messages=self.messages)
        self.meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        text = "".join(b.text for b in resp.content if b.type == "text")
        self.messages.append({"role": "assistant", "content": resp.content})
        return Reply(text)

    def _send_openai(self) -> Reply:
        kwargs = dict(model=MODEL, messages=self.messages)
        if IS_REASONING_MODEL:
            pass
        else:
            kwargs["temperature"] = TEMPERATURE
        resp = _get_client().chat.completions.create(**kwargs)
        usage = resp.usage
        self.meter.add(getattr(usage, "prompt_tokens", 0),
                        getattr(usage, "completion_tokens", 0))
        msg = resp.choices[0].message
        self.messages.append({"role": "assistant", "content": msg.content or ""})
        return Reply(msg.content or "")
