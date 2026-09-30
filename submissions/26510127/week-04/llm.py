"""Model call shared by the agents and the reader (OpenAI-compatible endpoint).

  AGENT_PROVIDER=openrouter (key: OPENROUTER_API_KEY) | groq (key: GROQ_API_KEY)
  AGENT_MODEL=openai/gpt-oss-20b  AGENT_TEMPERATURE=0  AGENT_REASONING_EFFORT=low
  AGENT_MODEL=mock -> scripted replies, no network (pipeline test only; never used for results)
"""
import json
import os
import random
import re
import time

PROVIDER = os.environ.get("AGENT_PROVIDER", "openrouter").lower()
BASE_URL = os.environ.get("OPENAI_BASE_URL") or {
    "openrouter": "https://openrouter.ai/api/v1", "groq": "https://api.groq.com/openai/v1"}[PROVIDER]
MODEL = os.environ.get("AGENT_MODEL", "openai/gpt-oss-20b")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
REASONING_EFFORT = os.environ.get("AGENT_REASONING_EFFORT", "low")
RETRIES = 8


class LLMError(RuntimeError):
    """Key, model, or quota problem: the runner stops instead of recording a crash row."""


class Meter:
    def __init__(self):
        self.calls = 0          # successful model calls (retries not counted)
        self.prompt_tokens = 0
        self.completion_tokens = 0


_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI
        var = {"openrouter": "OPENROUTER_API_KEY", "groq": "GROQ_API_KEY"}[PROVIDER]
        key = os.environ.get(var) or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError(f"set {var}")
        _client = OpenAI(base_url=BASE_URL, api_key=key, max_retries=0, timeout=120)
    return _client


def call_model(system: str, messages: list, meter: Meter) -> str:
    if MODEL == "mock":
        meter.calls += 1
        return _mock(system, messages)
    import openai
    kw = dict(model=MODEL, messages=[{"role": "system", "content": system}] + messages, temperature=TEMPERATURE)
    if "gpt-oss" in MODEL and REASONING_EFFORT:
        if "openrouter" in BASE_URL:      # reasoning stays out of the message text
            kw["extra_body"] = {"reasoning": {"effort": REASONING_EFFORT, "exclude": True}}
        else:
            kw["reasoning_effort"] = REASONING_EFFORT
    wait = 4.0
    for _ in range(RETRIES):
        try:
            resp = _get_client().chat.completions.create(**kw)
            if not getattr(resp, "choices", None):
                raise ValueError("response without choices")
            meter.calls += 1
            if resp.usage:
                meter.prompt_tokens += resp.usage.prompt_tokens or 0
                meter.completion_tokens += resp.usage.completion_tokens or 0
            return (resp.choices[0].message.content or "").strip()
        except openai.RateLimitError:
            time.sleep(wait + random.random())
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                time.sleep(wait + random.random())
            else:
                raise LLMError(f"{type(e).__name__}: {e}") from e
        except (openai.APIConnectionError, openai.APITimeoutError, ValueError):
            time.sleep(wait + random.random())
        wait = min(wait * 2, 90)
    raise LLMError(f"LLM failed after {RETRIES} attempts")


# ---------------------------------------------------------------- mock (pipeline test only)
def _mock(system: str, messages: list) -> str:
    if system.startswith("You are an observer"):          # reader
        last = messages[-1]["content"].rsplit("LAST MESSAGE:", 1)[-1]
        nums = [int(n) for n in re.findall(r"\d+", last)]
        perf = ("accept-proposal" if "deal" in last else "refuse" if "leave" in last
                else "propose" if nums else None)
        return json.dumps({"performative": perf, "price": nums[0] if nums else None})
    m = re.search(r"(?:at most|at least) (\d+)", system)
    limit, buyer = int(m.group(1)), system.startswith("You are the buyer")
    theirs = [int(n) for x in messages if x["role"] == "user" for n in re.findall(r"\d+", x["content"])]
    mine = [int(n) for x in messages if x["role"] == "assistant" for n in re.findall(r"\d+", x["content"])]
    if theirs and ((buyer and theirs[-1] <= limit) or (not buyer and theirs[-1] >= limit)):
        act, price, text = "accept-proposal", None, "OK, we have a deal."
    elif len(mine) >= 3:
        act, price, text = "refuse", None, "I will leave this negotiation."
    else:
        price = (limit - 20 + 5 * len(mine)) if buyer else (limit + 30 - 10 * len(mine))
        act, text = "propose", f"I can do {price}."
    if "exactly one JSON object" in system:
        return json.dumps({"performative": act, "content": {"price": price}})
    if "performative tag in parentheses" in system:
        return f"({act}) {text}"
    return text
