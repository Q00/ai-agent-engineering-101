"""Model access for the week-04 negotiation.

Grown from week-03's model.py, which was itself trimmed from week-02's
tools_shared.py. Three things are different this week and each one is here
for a reason the lab forced.

A conversation instead of a single call. Week 03's contractor saw one
announcement and answered once, so call_model took a system prompt and one
user string. A negotiation accumulates: each agent keeps its own history,
its own messages as assistant turns and the other side's as user turns.
chat() takes that list.

The provider is chosen explicitly, not inferred. Week 03 picked anthropic
whenever ANTHROPIC_API_KEY was present. Both keys are present on this
machine, so inference would silently pick the wrong one and the report's
setup section would be a lie. AGENT_PROVIDER decides, and it defaults to
the openai path because that is what OpenRouter speaks.

Rate limits are survived, not failed on. OpenRouter's free endpoints return
429 in bursts and a free-tier key stops at 50 free-model requests per day.
A 429 that ends an episode would silently bias the results toward whatever
condition happened to run first, so chat() waits and retries, and the
runner records what it could not finish rather than dropping the row.
"""

import json
import os
import random
import time
import urllib.error
import urllib.request

PROVIDER = os.environ.get("AGENT_PROVIDER", "openai")
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-haiku-4-5-20251001" if PROVIDER == "anthropic"
    else "nvidia/nemotron-3-super-120b-a12b:free")

# openai/OpenRouter path only. The anthropic path cannot set it on the models
# where sampling was removed; run_header() reports which case applies.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "300"))
EFFORT = os.environ.get("AGENT_EFFORT", "low")

# A 429 on a free endpoint is routine, not an error. Wait and try again.
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "6"))
BASE_BACKOFF = float(os.environ.get("AGENT_BASE_BACKOFF", "4"))

_client = None


class DailyQuotaExhausted(RuntimeError):
    """The free-model daily allowance is gone. Retrying cannot help today.

    Kept distinct from an ordinary rate limit because the runner has to treat
    it differently: a burst 429 is worth waiting out, a spent daily quota
    means stop and continue tomorrow with the same results.csv.
    """


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
    """Model calls, counted where they are spent.

    Week 02 and 03 counted one number. This week the report has to separate
    them: reader_calls is a measured variable of the experiment (it is the
    cost the explicit performative tag is supposed to save), while the
    agents' own calls are fixed by the turn limit and say nothing about the
    format. Summing them would hide exactly the quantity under test.
    """

    def __init__(self):
        self.tokens = 0
        self.agent_calls = 0
        self.reader_calls = 0

    @property
    def calls(self) -> int:
        return self.agent_calls + self.reader_calls

    def add(self, input_tokens: int, output_tokens: int, kind: str):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        if kind == "reader":
            self.reader_calls += 1
        else:
            self.agent_calls += 1


def _is_rate_limit(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status == 429 or "429" in str(exc) or "rate limit" in str(exc).lower()


def _is_daily_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    return "per day" in text or "daily limit" in text or "free-models-per-day" in text


def chat(system: str, messages: list, meter: Meter, kind: str = "agent") -> str:
    """One model call over a conversation. Returns the reply text, untruncated.

    Untruncated on purpose, for the reason week 03 recorded: week 02 cut each
    logged reply to 300 characters and the line that decided a run fell past
    the cut. Truncation belongs to the logger, never here.
    """
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            return _chat_once(system, messages, meter, kind)
        except Exception as exc:  # noqa: BLE001 - the provider SDKs raise their own types
            last = exc
            if _is_daily_quota(exc):
                raise DailyQuotaExhausted(str(exc)) from exc
            if not _is_rate_limit(exc):
                raise
            wait = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
            print(f"      [429] attempt {attempt + 1}/{MAX_RETRIES}, waiting {wait:.1f}s",
                  flush=True)
            time.sleep(wait)
    raise RuntimeError(f"rate limited {MAX_RETRIES} times in a row: {last}")


def _chat_once(system: str, messages: list, meter: Meter, kind: str) -> str:
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS,
            system=system, messages=messages)
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens, kind)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = _get_client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system}] + messages,
        # Reasoning models on OpenRouter put their thinking into the message
        # text unless this is off. A negotiation message with the model's
        # deliberation prepended parses as neither JSON nor a tagged line.
        extra_body={"reasoning": {"enabled": False}})
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0),
              getattr(usage, "completion_tokens", 0), kind)
    return resp.choices[0].message.content or ""


def free_quota_remaining():
    """What OpenRouter says is left of today's free-model allowance.

    Returns (used, limit) or None when the provider is not OpenRouter or the
    call fails. The runner prints it into the log header so a short run is
    legible later as a quota stop rather than as an unexplained gap.
    """
    if PROVIDER == "anthropic":
        return None
    base = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" not in base:
        return None
    key = os.environ.get("OPENAI_API_KEY", "")
    req = urllib.request.Request(
        base.rstrip("/") + "/key", headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)["data"]
        q = data.get("free_model_daily_requests") or {}
        return q.get("used"), q.get("limit")
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None


def run_header(turn_limit: int) -> str:
    """The first line of every log file: what produced the numbers below it."""
    import platform
    try:
        if PROVIDER == "anthropic":
            import anthropic
            sdk = f"anthropic {anthropic.__version__}"
        else:
            import openai
            sdk = f"openai {openai.__version__}"
    except Exception:  # noqa: BLE001
        sdk = "sdk version unavailable"
    if PROVIDER == "anthropic":
        sampling = (f"temperature=NOT_SETTABLE(sampling removed on {MODEL}; "
                    f"internal value unknown)")
    else:
        sampling = f"temperature={TEMPERATURE}"
    base = os.environ.get("OPENAI_BASE_URL", "(default)") if PROVIDER != "anthropic" else "-"
    quota = free_quota_remaining()
    quota_s = f" free_daily={quota[0]}/{quota[1]}" if quota else ""
    return (f"provider={PROVIDER} base_url={base} model={MODEL} {sampling} "
            f"max_tokens={MAX_TOKENS} turn_limit={turn_limit} "
            f"reasoning=disabled sdk={sdk} python={platform.python_version()}{quota_s}")
