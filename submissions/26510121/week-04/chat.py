"""One model call, one place. Copied from week-03 `chat.py` and extended.

Week 04 is a multi-turn negotiation, so three things changed:

  * `ask` takes a conversation (a list of {role, content}) instead of one user
    string. Each agent has to see what the other side actually said; the
    reader sees one message at a time. Both go through here.
  * a CLI backend (`AGENT_BACKEND=cli`). A free OpenRouter key stops at 50
    requests a day and the full lab is about 230, so the reference run went
    through the Claude Code CLI instead. Same `ask`, different transport.
  * HTTP 429 is retried with a growing wait. Free endpoints return it in
    bursts, and an episode lost halfway is an episode that has to be re-run.

Environment:
  AGENT_BACKEND     api (default) or cli
  OPENAI_API_KEY    required for the api backend; never read from a file, never logged
  OPENAI_BASE_URL   unset for OpenAI itself; https://openrouter.ai/api/v1 for OpenRouter
  AGENT_MODEL       default gpt-4o-mini (api) / haiku (cli)
  AGENT_TEMPERATURE default 0
  AGENT_MAX_TOKENS  default 400
  AGENT_RETRIES     default 5
"""
import os
import subprocess
import time

BACKEND = os.environ.get("AGENT_BACKEND", "api").strip().lower()
MODEL = os.environ.get("AGENT_MODEL") or ("haiku" if BACKEND == "cli" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "400"))
RETRIES = int(os.environ.get("AGENT_RETRIES", "5"))
BASE_URL = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"

_client = None
# set once a provider has refused `temperature`, or once the CLI backend is
# used at all: the report then has to say "not settable" instead of quoting a
# number we never actually sent
temperature_rejected = (BACKEND == "cli")


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()          # reads OPENAI_API_KEY / OPENAI_BASE_URL
    return _client


class Meter:
    """Tokens and model calls, counted in one place. Protocol messages are
    counted by the runner instead: a message is not always a model call, and
    that difference is the whole point of the `structured` condition."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


def settings_line() -> str:
    """First line of every log file. Everything a reader needs to repeat the
    run except the key itself."""
    temp = "not settable (backend does not expose it)" if temperature_rejected else TEMPERATURE
    where = BASE_URL if BACKEND == "api" else "claude -p"
    return (f"backend={BACKEND} endpoint={where} model={MODEL} "
            f"temperature={temp} max_tokens={MAX_TOKENS}")


def _is_rate_limit(err: Exception) -> bool:
    text = str(err).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _call_api(system: str, messages: list, meter: Meter) -> str:
    global temperature_rejected
    kwargs = dict(model=MODEL,
                  messages=[{"role": "system", "content": system}] + list(messages),
                  max_tokens=MAX_TOKENS,
                  temperature=TEMPERATURE,
                  # reasoning models put their thinking into the message text
                  # unless this is off, which the protocol layer would then
                  # have to parse as if it were a negotiation move
                  extra_body={"reasoning": {"enabled": False}})
    try:
        resp = _get_client().chat.completions.create(**kwargs)
    except Exception as e:
        if "temperature" not in str(e):
            raise
        # reasoning models reject a non-default temperature; retry once
        # without it and record that the control variable was not settable
        temperature_rejected = True
        kwargs.pop("temperature")
        resp = _get_client().chat.completions.create(**kwargs)
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


def _call_cli(system: str, messages: list, meter: Meter) -> str:
    """`claude -p`, prompt on stdin. The CLI exposes no temperature and no
    token usage, so both are recorded as unknown rather than guessed."""
    parts = [system, ""]
    for m in messages:
        who = "OTHER PARTY" if m["role"] == "user" else "YOU SAID EARLIER"
        parts.append(f"{who}: {m['content']}")
    parts.append("YOUR REPLY (one message, nothing else):")
    prompt = "\n".join(parts)
    proc = subprocess.run(["claude", "-p", "--model", MODEL],
                          input=prompt, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {proc.stderr.strip()[:200]}")
    meter.add(0, 0)
    return (proc.stdout or "").strip()


def ask(system: str, messages: list, meter: Meter, role: str = None) -> str:
    """One turn. `messages` is the conversation so far from this speaker's
    point of view. `role` is ignored here; it exists so that the offline fake
    provider can tell buyer, seller and reader apart with the same signature."""
    wait = 5
    for attempt in range(RETRIES):
        try:
            if BACKEND == "cli":
                return _call_cli(system, messages, meter)
            return _call_api(system, messages, meter)
        except Exception as e:
            if not _is_rate_limit(e) or attempt == RETRIES - 1:
                raise
            print(f"      (rate limited, waiting {wait}s)", flush=True)
            time.sleep(wait)
            wait = min(wait * 2, 120)
    raise RuntimeError("unreachable")
