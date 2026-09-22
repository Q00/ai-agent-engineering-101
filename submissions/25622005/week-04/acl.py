"""Week 04 — the model call, the prompts, and the protocol layer.

The independent variable of the lab is the message format, so everything that
is not the format lives here once and is shared by the three conditions:
the role paragraph, the four-act paragraph, the reader prompt, the model, the
temperature, and the turn limit. Only FORMAT[condition] and read() differ.

The model call follows week-03's contractor.py, which trimmed week-02's
starter Chat to the OpenAI-compatible path, plus a 429 backoff because free
OpenRouter endpoints rate-limit in bursts.

Provider is picked from the environment, same rule as the week-02 starter:
  ANTHROPIC_API_KEY set  -> Anthropic SDK (ANTHROPIC_BASE_URL honoured)
  otherwise              -> OpenAI-compatible (OPENAI_API_KEY, OPENAI_BASE_URL;
                            https://openrouter.ai/api/v1 for OpenRouter)
  AGENT_MODEL            model id for either provider
"""
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

# ---- control variables: fixed across every condition, reported in REPORT.md ----
PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get("AGENT_MODEL", "gpt-4o-mini")
TEMPERATURE = 0
MAX_TOKENS = 400        # roomy: a truncated reply would be a format error
                        # caused by this cap, not by the format paragraph
MAX_TURNS = 8           # messages per episode; past this the outcome is `open`

# anthropic 1.4.0's Messages.create takes no `temperature`; week-02's
# tools_shared.py does not send one either. On that path the runs use the
# provider default, and REPORT.md has to say so rather than claim temp=0.
TEMPERATURE_SENT = TEMPERATURE if PROVIDER == "openai" else "provider-default"

CONDITIONS = ("free", "tagged", "structured")
ACTS = ("propose", "accept-proposal", "reject-proposal", "refuse")

RETRIES = 5             # 429 comes in bursts on free endpoints
BACKOFF = 4             # seconds, doubled each retry


class Meter:
    """Tokens and calls, counted in one place (week-03's Meter, plus the one
    number this week asks for separately: calls spent reading messages)."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.reader_calls = 0

    def add(self, input_tokens, output_tokens):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


# ---- model ----

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()   # ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
        else:
            from openai import OpenAI
            _client = OpenAI()                # OPENAI_API_KEY / OPENAI_BASE_URL
    return _client


def _openrouter() -> bool:
    return "openrouter" in os.environ.get("OPENAI_BASE_URL", "")


def _is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or "429" in str(e)


def _once(system: str, messages: list, meter: Meter) -> str:
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(     # no temperature: see above
            model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")
    kwargs = dict(model=MODEL, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
                  messages=[{"role": "system", "content": system}] + messages)
    if _openrouter():
        # a reasoning model puts its thinking into the message text otherwise,
        # which every one of the three formats would read as a format error
        kwargs["extra_body"] = {"reasoning": {"enabled": False}}
    resp = _get_client().chat.completions.create(**kwargs)
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


def call_model(system: str, messages: list, meter: Meter, log=print) -> str:
    """One model call, retried with a growing wait on 429 so a rate limit costs
    time and not an episode."""
    for attempt in range(RETRIES):
        try:
            return _once(system, messages, meter)
        except Exception as e:
            if not _is_rate_limit(e) or attempt == RETRIES - 1:
                raise
            wait = BACKOFF * 2 ** attempt
            log(f"  [429] waiting {wait}s, retry {attempt + 1}/{RETRIES - 1}")
            time.sleep(wait)


# ---- prompts: the role paragraph is common, only the format paragraph varies ----

ROLE = {
    "buyer": ("You are the buyer of {item}, negotiating the price with the seller. "
              "Your private limit: you can pay at most {limit}. Never agree to a price "
              "above {limit}, and never reveal the number itself. You open the negotiation."),
    "seller": ("You are the seller of {item}, negotiating the price with the buyer. "
               "Your private limit: you can accept at least {limit}. Never agree to a price "
               "below {limit}, and never reveal the number itself."),
}

COMMON = (" Four acts are available: propose (offer a price), accept-proposal (agree to the "
          "other side's last price, which ends the negotiation with a deal), reject-proposal "
          "(decline the last price and keep negotiating), refuse (leave the negotiation for "
          "good, no deal). Every message you send is exactly one of these four acts.")

FORMAT = {
    "free": " Write your message as one or two plain English sentences.",
    "tagged": (" Start your message with exactly one performative tag in parentheses, one of "
               "(propose), (accept-proposal), (reject-proposal), (refuse), then write one "
               "plain English sentence."),
    "structured": (' Reply with exactly one JSON object and nothing else: '
                   '{"performative": "propose" | "accept-proposal" | "reject-proposal" | '
                   '"refuse", "content": {"price": <whole number or null>}}.'),
}


def system_prompt(role: str, item: str, limit: int, condition: str) -> str:
    return ROLE[role].format(item=item, limit=limit) + COMMON + FORMAT[condition]


READER_SYSTEM = (
    "You are an observer reading a price negotiation between a buyer and a seller. "
    "Label the LAST message only. Reply with exactly one JSON object and nothing else: "
    '{"performative": "propose" | "accept-proposal" | "reject-proposal" | "refuse", '
    '"price": <whole number or null>}. The price is the number the last message puts on '
    "the table, or null if it names none. Pick the single act that best fits the last "
    "message; there is no other choice available.")


# ---- protocol layer: three ways to read the same four acts ----

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_OBJECT = re.compile(r"\{.*\}", re.S)
_TAG = re.compile(r"^\s*\(([a-z][a-z-]*)\)")


def _loads(text: str):
    """Parse a JSON object out of a reply, with week-03's two levels of
    leniency: strip a ```json fence, then take the outermost {...}. Anything
    past that counts as unreadable, because hiding it would hide format_errors."""
    text = (text or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    match = _OBJECT.search(text)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _price(value):
    """An int price, or None. Floats and numeric strings are accepted because
    the format paragraph asks for a whole number and the model is the one that
    sometimes writes "120" or 120.0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("$").isdigit():
        return int(value.strip().lstrip("$"))
    return None


def _reader(transcript: list, meter: Meter, log=print):
    """One model call that labels the last message. Same prompt in every
    condition, so the reader is a control variable and not part of the format."""
    lines = "\n".join(f"{speaker}: {text}" for speaker, text in transcript)
    meter.reader_calls += 1
    raw = call_model(READER_SYSTEM, [{"role": "user", "content": lines}], meter, log)
    obj = _loads(raw)
    if obj is None:
        return None, None
    perf = obj.get("performative")
    return (perf if perf in ACTS else None), _price(obj.get("price"))


def read(condition: str, text: str, transcript: list, meter: Meter, log=print):
    """Return (performative, price, ok). ok is False when this layer could not
    read the message; the caller counts it in format_errors and passes the
    message to the other agent unchanged either way."""
    if condition == "structured":
        obj = _loads(text)
        if obj is None:
            return None, None, False
        perf = obj.get("performative")
        if perf not in ACTS:
            return None, None, False
        content = obj.get("content") or {}
        price = _price(content.get("price") if isinstance(content, dict) else None)
        # a propose with no number is not a proposal this layer can act on
        return (perf, price, price is not None) if perf == "propose" else (perf, price, True)

    if condition == "tagged":
        match = _TAG.match(text or "")
        perf = match.group(1) if match else None
        if perf not in ACTS:
            return None, None, False
        if perf != "propose":
            return perf, None, True
        _, price = _reader(transcript, meter, log)   # the tag is read, only the price is not
        return perf, price, price is not None

    perf, price = _reader(transcript, meter, log)     # free: nothing but the reader
    return perf, price, perf is not None
