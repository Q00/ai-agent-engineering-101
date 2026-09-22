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

The OpenAI-compatible path speaks HTTP directly instead of through the
`openai` package. That was not a preference. The runs here go out through
an intercepting TLS proxy, and the installed SDK's transport (httpx2, which
verifies through macOS Secure Transport) rejects its certificate chain with
OSStatus -26276 while the standard library's ssl module accepts it. The
alternative was to pass verify=False, which would have put a disabled
certificate check into the submitted code to work around a condition local
to one machine. Sixty lines of urllib keeps the verification on, drops the
dependency, and runs on any Python 3 without a virtualenv.
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

# Settable on both paths used here. Week 03 had to record temperature as not
# settable because sampling is removed on claude-sonnet-5; haiku 4.5 takes it,
# so this run can pin it and say so.
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "300"))

# A 429, or a busy upstream, is routine rather than an error. Wait and retry.
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "6"))
BASE_BACKOFF = float(os.environ.get("AGENT_BASE_BACKOFF", "4"))


class DailyQuotaExhausted(RuntimeError):
    """The free-model daily allowance is gone. Retrying cannot help today.

    Kept distinct from an ordinary rate limit because the runner has to treat
    it differently: a burst 429 is worth waiting out, a spent daily quota
    means stop and continue tomorrow with the same results.csv.
    """


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 120) -> dict:
    """Both providers go out this way. Neither SDK is used; see the docstring."""
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise ApiError(e.code, f"HTTP {e.code} from {url}: {body}") from None


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status_code = status


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


def _is_retryable(exc: Exception) -> bool:
    """A 429, or an upstream that is merely busy.

    OpenRouter returns some upstream failures as HTTP 200 with an `error`
    object in the body, so status alone is not enough. The first real run
    here died on {"code": 503, "error_type": "provider_overloaded"} two
    messages into an episode, which is a queue being full, not an answer.
    Treating it as fatal would have thrown away the calls already spent on
    that episode and, worse, biased the results toward whichever condition
    happened to run when the free pool was quiet.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 429 or (isinstance(status, int) and 500 <= status < 600):
        return True
    text = str(exc).lower()
    return ("429" in text or "rate limit" in text
            or "overloaded" in text or "temporarily unavailable" in text)


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
            if not _is_retryable(exc):
                raise
            wait = BASE_BACKOFF * (2 ** attempt) + random.uniform(0, 1)
            status = getattr(exc, "status_code", "?")
            print(f"      [{status}] attempt {attempt + 1}/{MAX_RETRIES}, "
                  f"waiting {wait:.1f}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"rate limited {MAX_RETRIES} times in a row: {last}")


def _chat_once(system: str, messages: list, meter: Meter, kind: str) -> str:
    if PROVIDER == "anthropic":
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
        data = _post_json(base + "/v1/messages", {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "system": system,
            "messages": messages,
        }, {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01"})
        usage = data.get("usage") or {}
        meter.add(usage.get("input_tokens", 0), usage.get("output_tokens", 0), kind)
        if data.get("type") == "error":
            raise ApiError(0, f"upstream error: {json.dumps(data.get('error'))[:300]}")
        return "".join(b.get("text", "") for b in data.get("content", [])
                       if b.get("type") == "text")

    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data = _post_json(base + "/chat/completions", {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "messages": [{"role": "system", "content": system}] + messages,
        # Reasoning models on OpenRouter put their thinking into the message
        # text unless this is off. A negotiation message with the model's
        # deliberation prepended parses as neither JSON nor a tagged line.
        "reasoning": {"enabled": False},
    }, {"Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "")})
    # An upstream failure can arrive as HTTP 200 with an error object and no
    # choices. It is charged as a request either way, so the meter is fed
    # before the error is raised.
    usage = data.get("usage") or {}
    meter.add(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), kind)
    err = data.get("error")
    if err:
        raise ApiError(int(err.get("code") or 0), f"upstream error: {json.dumps(err)[:300]}")
    choices = data.get("choices") or []
    if not choices:
        raise ApiError(0, f"no choices in response: {json.dumps(data)[:300]}")
    return (choices[0].get("message") or {}).get("content") or ""


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
    if PROVIDER == "anthropic":
        base = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        extra = "anthropic-version=2023-06-01"
    else:
        base = os.environ.get("OPENAI_BASE_URL", "(default)")
        extra = "reasoning=disabled"
    quota = free_quota_remaining()
    quota_s = f" free_daily={quota[0]}/{quota[1]}" if quota else ""
    return (f"provider={PROVIDER} base_url={base} model={MODEL} "
            f"temperature={TEMPERATURE} max_tokens={MAX_TOKENS} "
            f"turn_limit={turn_limit} {extra} transport=stdlib-urllib "
            f"python={platform.python_version()}{quota_s}")
