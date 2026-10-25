"""The harness both agents call: one prompt in, one JSON decision out.

The harness is my own minimal loop around a local model: Qwen/Qwen3.8-27B on vLLM
(serve_model.sh), called through the OpenAI-compatible chat API with thinking off.
Every price decision of the seller and the buyer comes from here; the agents' code
only speaks A2A and enforces the hard limits (reserve, budget).

  HARNESS_BASE_URL   default http://127.0.0.1:8011/v1
  HARNESS_MODEL      default Qwen/Qwen3.8-27B
  HARNESS_TEMPERATURE default 0.2
"""
import json
import os
import re

from openai import AsyncOpenAI

BASE_URL = os.environ.get("HARNESS_BASE_URL", "http://127.0.0.1:8011/v1")
MODEL = os.environ.get("HARNESS_MODEL", "Qwen/Qwen3.8-27B")
TEMPERATURE = float(os.environ.get("HARNESS_TEMPERATURE", "0.2"))
MAX_TOKENS = 300
NAME = f"own harness (harness.py) -> {MODEL} on local vLLM, temperature {TEMPERATURE}, thinking off"

_client = None


def _llm():
    global _client
    if _client is None:
        _client = AsyncOpenAI(base_url=BASE_URL, api_key=os.environ.get("HARNESS_API_KEY", "none"), timeout=120)
    return _client


def _parse(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


async def decide(system: str, user: str, log=print, tag: str = "harness") -> dict:
    """Ask the model for a JSON object. One retry if the answer is not JSON; {} if both fail."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        resp = await _llm().chat.completions.create(
            model=MODEL, messages=messages, temperature=TEMPERATURE, max_tokens=MAX_TOKENS,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        raw = resp.choices[0].message.content or ""
        obj = _parse(raw)
        log(f"[{tag}] {' '.join(raw.split())[:400]}")
        if obj is not None:
            return obj
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Answer with exactly one JSON object and nothing else."}]
    return {}
