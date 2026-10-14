"""Shared OpenAI-compatible model client for the Week 05 experiment."""

from __future__ import annotations

import os
import json
import time
from dataclasses import dataclass
from typing import Callable, Literal


Purpose = Literal["agent", "reader"]
RetryObserver = Callable[[int, int, float], None]


class RateLimitError(RuntimeError):
    """A retryable HTTP 429 returned by the configured provider."""


class ProviderResponseError(RuntimeError):
    """A successful HTTP response without a usable chat completion."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolDecision:
    content: str | None
    calls: tuple[ToolCall, ...]


def redact_secrets(value: object) -> str:
    """Remove the configured API key from text before it reaches logs or CSV."""
    text = str(value)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    return text.replace(api_key, "[REDACTED]") if api_key else text


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str
    temperature: float
    max_tokens: int
    max_retries: int
    backoff_seconds: float

    @classmethod
    def from_env(cls) -> "ModelConfig":
        return cls(
            provider=os.environ.get("AGENT_PROVIDER", "ollama-local"),
            model=os.environ.get("AGENT_MODEL", "qwen2.5:7b-instruct"),
            base_url=os.environ.get(
                "OPENAI_BASE_URL", "http://localhost:11434/v1"
            ),
            temperature=float(os.environ.get("AGENT_TEMPERATURE", "0")),
            max_tokens=int(os.environ.get("AGENT_MAX_TOKENS", "512")),
            max_retries=int(os.environ.get("AGENT_MAX_RETRIES", "3")),
            backoff_seconds=float(os.environ.get("AGENT_BACKOFF_SECONDS", "2")),
        )


@dataclass
class CallMeter:
    agent_calls: int = 0
    reader_calls: int = 0

    def record(self, purpose: Purpose) -> None:
        if purpose == "agent":
            self.agent_calls += 1
        elif purpose == "reader":
            self.reader_calls += 1
        else:  # pragma: no cover - guarded by the Purpose type
            raise ValueError(f"unknown call purpose: {purpose}")


def is_rate_limit_error(exc: BaseException) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) == 429


def is_server_error(exc: BaseException) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and 500 <= status_code <= 599


def retry_after_seconds(exc: BaseException) -> float | None:
    """Return a non-negative numeric Retry-After value when one is available."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        value = headers.get("retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class ModelClient:
    """One client/config shared by buyer and seller calls."""

    def __init__(
        self,
        config: ModelConfig,
        meter: CallMeter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: RetryObserver | None = None,
    ) -> None:
        self.config = config
        self.meter = meter or CallMeter()
        self._sleep = sleep
        self._on_retry = on_retry or (lambda _retry, _maximum, _delay: None)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            self._client = OpenAI(
                api_key=api_key,
                base_url=self.config.base_url,
                timeout=60.0,
                max_retries=0,
            )
        return self._client

    def complete(self, messages: list[dict[str, str]], purpose: Purpose) -> str:
        """Call the model and return a text response."""
        message = self._completion(messages, purpose=purpose)
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            raise ProviderResponseError(
                "provider response message has no text content"
            )
        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
    ) -> ToolDecision:
        """Return a normalized tool decision for the Week 01-style host loop."""
        message = self._completion(messages, purpose="agent", tools=tools)
        calls = []
        for call in getattr(message, "tool_calls", None) or []:
            try:
                arguments = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ProviderResponseError("tool call arguments are not valid JSON") from exc
            if not isinstance(arguments, dict):
                raise ProviderResponseError("tool call arguments must be a JSON object")
            calls.append(
                ToolCall(
                    id=str(call.id),
                    name=str(call.function.name),
                    arguments=arguments,
                )
            )
        content = getattr(message, "content", None)
        if content is not None and not isinstance(content, str):
            raise ProviderResponseError("provider response content has an invalid type")
        return ToolDecision(content=content, calls=tuple(calls))

    def _completion(
        self,
        messages: list[dict],
        *,
        purpose: Purpose,
        tools: list[dict] | None = None,
    ):
        """Make one logical completion with bounded retries."""
        for attempt in range(self.config.max_retries + 1):
            self.meter.record(purpose)
            try:
                provider_options = (
                    {"extra_body": {"reasoning": {"enabled": False}}}
                    if self.config.provider == "openrouter"
                    else {}
                )
                response = self._get_client().chat.completions.create(
                    model=self.config.model,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    messages=messages,
                    **({"tools": tools} if tools is not None else {}),
                    **provider_options,
                )
            except Exception as exc:
                retryable = is_rate_limit_error(exc) or is_server_error(exc)
                if not retryable:
                    raise
                if attempt >= self.config.max_retries:
                    if is_rate_limit_error(exc):
                        raise RateLimitError(
                            f"provider returned HTTP 429 after {attempt + 1} attempts"
                        ) from exc
                    raise
                delay = retry_after_seconds(exc)
                if delay is None:
                    delay = self.config.backoff_seconds * (attempt + 1)
                self._on_retry(attempt + 1, self.config.max_retries, delay)
                self._sleep(delay)
                continue
            choices = getattr(response, "choices", None)
            if not isinstance(choices, list) or not choices:
                error = ProviderResponseError("provider response has no completion choices")
                if attempt >= self.config.max_retries:
                    raise error
                delay = self.config.backoff_seconds * (attempt + 1)
                self._on_retry(attempt + 1, self.config.max_retries, delay)
                self._sleep(delay)
                continue
            message = getattr(choices[0], "message", None)
            if message is None:
                raise ProviderResponseError(
                    "provider response choice has no message"
                )
            return message
        raise AssertionError("retry loop exhausted unexpectedly")
