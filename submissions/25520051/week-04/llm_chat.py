"""Week 04 — model call and meter, adapted from week-03's llm_chat.py.

Same Chat/Meter shape, plus a third provider: shelling out to the Claude
Code CLI (`claude -p`) when no API key is set in the environment. This
repo's README recommends exactly this for week 04's reference run, and it
lets the negotiation run without asking for or hardcoding a key.

Provider is picked from the environment:
  ANTHROPIC_API_KEY set          -> Anthropic SDK (pip install anthropic)
  elif OPENAI_API_KEY set        -> OpenAI SDK (pip install openai); set
                                    OPENAI_BASE_URL to point at an
                                    OpenAI-compatible provider (e.g. OpenRouter)
  else                           -> `claude -p` subprocess (needs the Claude
                                    Code CLI on PATH and an active login)
  AGENT_MODEL                    optional model override for any provider
                                    (default: gpt-5-mini for openai, haiku
                                    alias for anthropic/cli)
  AGENT_TEMPERATURE              optional float; ignored for reasoning
                                    models (gpt-5*, o1*, o3*, o4*) and for
                                    the cli provider, which exposes no
                                    temperature flag at all
"""
import os
import subprocess
from dataclasses import dataclass

if os.environ.get("ANTHROPIC_API_KEY"):
    PROVIDER = "anthropic"
elif os.environ.get("OPENAI_API_KEY"):
    PROVIDER = "openai"
else:
    PROVIDER = "cli"

MODEL = os.environ.get(
    "AGENT_MODEL",
    "gpt-5-mini" if PROVIDER == "openai" else "haiku")
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
    """Tokens and model-call count, same idea as weeks 02-03's Meter.

    The cli provider reports no usage, so tokens stay 0 for it; calls is
    still accurate since Meter.add() always increments it.
    """

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
    """One conversation with the model: a system prompt plus user turns.

    For the cli provider there is no persistent session, so each send()
    replays the whole transcript (this side's replies as "You", the other
    side's as "Them") as a single -p prompt; the system prompt still goes
    through --system-prompt so it is never mixed into the transcript text.
    """

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
        if PROVIDER == "cli":
            return self._send_cli()
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

    def _send_cli(self) -> Reply:
        # A single user turn (e.g. a one-shot reader call) goes through as
        # its own text -- the You/Them transcript framing below is only for
        # genuine multi-turn dialogue, and wrapping a one-shot instruction in
        # it makes the model treat "You:" as a cue to ask for the message
        # instead of just answering.
        if len(self.messages) == 1 and self.messages[0]["role"] == "user":
            prompt = self.messages[0]["content"]
        else:
            lines = []
            for m in self.messages:
                speaker = "You" if m["role"] == "assistant" else "Them"
                lines.append(f"{speaker}: {m['content']}")
            prompt = "\n\n".join(lines) + "\n\nYou:"
        cmd = ["claude", "-p", prompt, "--model", MODEL,
               "--system-prompt", self.system]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"claude -p exited {result.returncode}: {result.stderr[:500]}")
        text = result.stdout.strip()
        self.meter.add(0, 0)
        self.messages.append({"role": "assistant", "content": text})
        return Reply(text)
