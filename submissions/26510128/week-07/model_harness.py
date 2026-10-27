"""OpenAI-compatible LLM decision adapter shared by Week 06 agents.

The model chooses actions; callers validate protocol and financial invariants.
No external API credential is needed with the default local Ollama endpoint.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from openai import OpenAI

MODEL = os.getenv("AGENT_MODEL", "qwen3:4b-instruct")
BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.getenv("AGENT_MAX_TOKENS", "300"))
MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))


def _parse_json(message: str) -> dict[str, Any]:
    body = (message or "").strip()
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", body, flags=re.DOTALL)
        if not match:
            raise ValueError(f"not a JSON object: {body[:180]!r}")
        obj = json.loads(match.group())
    if not isinstance(obj, dict):
        raise ValueError("model must return a JSON object")
    return obj


def decide(system_prompt: str, state: dict[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    """Ask the harness to choose one negotiation act as a JSON object."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "ollama"), base_url=BASE_URL)
    request: dict[str, Any] = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(state, ensure_ascii=False)
                + "\nReturn only one JSON object with action and price (if applicable).",
            },
        ],
    }
    if "openrouter.ai" in BASE_URL.lower():
        request["extra_body"] = {"reasoning": {"enabled": False}}

    last_error: Exception | None = None
    for retry in range(MAX_RETRIES):
        try:
            result = client.chat.completions.create(**request)
            if not result.choices or not result.choices[0].message:
                raise ValueError("no choices")
            payload = _parse_json(result.choices[0].message.content or "")
            action = str(payload.get("action", "")).lower().strip()
            if action not in allowed:
                raise ValueError(f"invalid action {action!r}; allowed {allowed}")
            payload["action"] = action
            print(f"[harness] model={MODEL} action={action} price={payload.get('price')}", flush=True)
            return payload
        except Exception as exc:
            last_error = exc
            print(f"[harness error] attempt={retry + 1}: {type(exc).__name__}: {exc}", flush=True)
            if retry < MAX_RETRIES - 1:
                time.sleep(min(2 * (retry + 1), 5))
    raise RuntimeError(f"model could not produce a valid action: {last_error}")


def whole_price(data: dict[str, Any], key: str = "price") -> int:
    value = data.get(key)
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    number = int(value)
    if number < 0 or str(value).strip() not in {str(number), f"{number}.0"}:
        raise ValueError(f"invalid whole-number price {value!r}")
    return number
