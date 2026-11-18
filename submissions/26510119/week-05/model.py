import os
import time

from openai import OpenAI

PROVIDER = "openrouter"
MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
TEMPERATURE = 0.7

_client = None


def get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"),
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    return _client


def _retryable(err):
    s = str(err)
    return "429" in s or "empty choices" in s or any(f"Error code: {c}" in s for c in (500, 502, 503, 504))


def call_model(messages, tools, max_retries=6):
    """One chat completion with tools. Returns the assistant message."""
    kwargs = dict(model=MODEL, messages=messages, tools=tools, temperature=TEMPERATURE, max_tokens=512)
    for attempt in range(max_retries):
        try:
            try:
                resp = get_client().chat.completions.create(**kwargs, extra_body={"reasoning": {"enabled": False}})
            except Exception as e:
                if "reasoning" not in str(e).lower():
                    raise
                resp = get_client().chat.completions.create(**kwargs)
            if not resp.choices:
                raise RuntimeError("API returned empty choices")
            return resp.choices[0].message
        except Exception as e:
            if _retryable(e) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                print(f"  [retry] {str(e)[:160]} - waiting {wait}s", flush=True)
                time.sleep(wait)
            else:
                raise
