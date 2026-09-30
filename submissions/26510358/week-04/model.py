"""One OpenAI-compatible chat endpoint shared by both agents and the reader."""

import os
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    reasoning_effort: str

    @classmethod
    def from_env(cls):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required")
        provider = os.getenv("AGENT_PROVIDER", "openai")
        if provider != "openai":
            raise ValueError("Only an OpenAI-compatible Chat Completions endpoint is supported")
        settings = cls(
            provider=provider,
            model=os.getenv("AGENT_MODEL", "gpt-5.6-luna"),
            temperature=float(os.getenv("AGENT_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "512")),
            reasoning_effort=os.getenv("AGENT_REASONING_EFFORT", "none"),
        )
        if not 0 <= settings.temperature <= 2 or settings.max_tokens < 1:
            raise ValueError("Invalid temperature or max token setting")
        return settings


class Chat:
    def __init__(self, settings):
        from openai import OpenAI

        self.settings = settings
        self.client = OpenAI(timeout=60, max_retries=0)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(self, system, messages, log):
        from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

        retryable = (APIConnectionError, APITimeoutError, RateLimitError)
        for attempt in range(6):
            self.calls += 1
            try:
                response = self.client.chat.completions.create(
                    model=self.settings.model,
                    temperature=self.settings.temperature,
                    max_completion_tokens=self.settings.max_tokens,
                    reasoning_effort=self.settings.reasoning_effort,
                    messages=[{"role": "system", "content": system}, *messages],
                )
                usage = response.usage
                if usage:
                    self.input_tokens += usage.prompt_tokens
                    self.output_tokens += usage.completion_tokens
                choice = response.choices[0]
                log(f"[api] id={response.id} finish={choice.finish_reason} "
                    f"input={usage.prompt_tokens if usage else '?'} "
                    f"output={usage.completion_tokens if usage else '?'}")
                return choice.message.content or ""
            except APIStatusError as exc:
                if exc.status_code not in (429, 500, 502, 503, 504):
                    raise
                error = exc
            except retryable as exc:
                error = exc
            if attempt == 5:
                raise error
            delay = min(2 ** attempt, 16)
            log(f"[api-retry] attempt={attempt + 1} "
                f"error={type(error).__name__} wait={delay}s")
            time.sleep(delay)
        raise RuntimeError("Unreachable retry state")
