"""OpenAI-compatible model calls for the week-04 experiment."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable, Sequence


MODEL = os.environ.get(
    "AGENT_MODEL",
    "nvidia/nemotron-3-super-120b-a12b:free",
)
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0.0"))
PACE_SECONDS = float(os.environ.get("AGENT_PACE_SECONDS", "3.5"))
MAX_ATTEMPTS = int(os.environ.get("AGENT_MAX_ATTEMPTS", "4"))
PROVIDER = "OpenRouter" if "openrouter.ai" in os.environ.get(
    "OPENAI_BASE_URL", ""
) else "OpenAI-compatible"

LogCall = Callable[[str], None]
Message = dict[str, str]

_client = None
_last_call_at = 0.0


@dataclass
class Meter:
    calls: int = 0
    tokens: int = 0
    retries: int = 0


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI()
    return _client


def _pace() -> None:
    wait = PACE_SECONDS - (time.time() - _last_call_at)
    if wait > 0:
        time.sleep(wait)


def call_model(
    system: str,
    messages: Sequence[Message],
    meter: Meter,
    log: LogCall = print,
    max_tokens: int = 300,
) -> str:
    """Call one chat model with bounded retry and shared rate pacing."""

    global _last_call_at
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _pace()
        try:
            response = _get_client().chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, *messages],
                extra_body={"reasoning": {"enabled": False}},
            )
            _last_call_at = time.time()
            usage = response.usage
            meter.calls += 1
            meter.tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
            meter.tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            return response.choices[0].message.content or ""
        except Exception as exc:
            _last_call_at = time.time()
            last_error = exc
            meter.retries += 1
            if attempt == MAX_ATTEMPTS:
                break
            backoff = 5 * attempt
            log(
                f"  [retry {attempt}/{MAX_ATTEMPTS - 1}] "
                f"{type(exc).__name__}: {str(exc)[:180]} -- sleeping {backoff}s"
            )
            time.sleep(backoff)
    assert last_error is not None
    raise last_error
