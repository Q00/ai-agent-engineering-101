"""OpenAI adapter; physical retries and missing usage remain observable."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

DEFAULT_MODEL = "gpt-5.4-mini"


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = 0
    output_tokens: int | None = 0

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None or self.output_tokens is None:
            return None
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        def add(left: int | None, right: int | None) -> int | None:
            return None if left is None or right is None else left + right
        return Usage(add(self.input_tokens, other.input_tokens),
                     add(self.output_tokens, other.output_tokens))


@dataclass(frozen=True)
class ModelReply:
    text: str
    usage: Usage = Usage()
    retries: int = 0
    model: str | None = None


class Backend(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> ModelReply: ...


class ModelCallError(RuntimeError):
    """Safe metadata only: provider exception bodies may contain credentials."""

    def __init__(self, error_type: str, status_code: int | None, retries: int):
        self.error_type = error_type
        self.status_code = status_code
        self.retries = retries
        self.attempts = retries + 1
        self.usage = Usage(None, None)
        super().__init__(f"{error_type};status={status_code};attempts={self.attempts}")


class OpenAIBackend:
    def __init__(
        self, model: str = DEFAULT_MODEL, temperature: float = 0.2,
        reasoning_effort: str = "none", request_timeout: float = 90.0,
        max_retries: int = 4, backoff_base: float = 1.0,
        max_completion_tokens: int = 500, on_retry: Callable | None = None,
        sleeper: Callable[[float], None] = time.sleep, client: Any = None,
    ):
        self.model = model
        self.temperature = temperature
        self.reasoning_effort = reasoning_effort
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_completion_tokens = max_completion_tokens
        self.on_retry = on_retry or (lambda event: None)
        self.sleeper = sleeper
        self._client = client
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        url = urlsplit(self.base_url)
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("base URL must not contain credentials, query, or fragment")
        self.provider = ("openai" if url.hostname == "api.openai.com" else
                         "openrouter" if url.hostname == "openrouter.ai" else
                         "openai-compatible")

    def configuration(self) -> dict:
        return {
            "provider": self.provider, "base_url": self.base_url,
            "model": self.model, "temperature": self.temperature,
            "reasoning_effort": self.reasoning_effort,
            "max_completion_tokens": self.max_completion_tokens,
            "request_timeout": self.request_timeout, "max_retries": self.max_retries,
            "backoff_base": self.backoff_base, "sdk_retries": 0, "seed": None,
        }

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return getattr(exc, "status_code", None) in {408, 409, 429, 500, 502, 503, 504} or type(exc).__name__ in {
            "APIConnectionError", "APITimeoutError", "InternalServerError", "RateLimitError"
        }

    def complete(self, messages: list[dict[str, str]]) -> ModelReply:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.base_url, timeout=self.request_timeout, max_retries=0
            )
        kwargs = {
            "model": self.model, "messages": messages, "temperature": self.temperature,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if self.provider == "openrouter":
            kwargs["extra_body"] = {"reasoning": {"enabled": False}}
        else:
            kwargs["reasoning_effort"] = self.reasoning_effort
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                if attempt >= self.max_retries or not self._retryable(exc):
                    raise ModelCallError(type(exc).__name__, getattr(exc, "status_code", None), attempt) from None
                delay = min(self.backoff_base * 2**attempt, 30.0)
                self.on_retry({
                    "event": "model_retry", "retry": attempt + 1,
                    "delay_seconds": delay, "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                })
                self.sleeper(delay)
                continue
            usage = response.usage
            reply = ModelReply(
                response.choices[0].message.content or "",
                Usage(getattr(usage, "prompt_tokens", None),
                      getattr(usage, "completion_tokens", None)),
                attempt, getattr(response, "model", None),
            )
            return reply
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
