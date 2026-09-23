"""OpenAI transport and bounded retry policy for the live experiment."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, assert_never

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from acl_lab.domain import (
    ChatMessage,
    ChatRole,
    ModelClient,
    ModelError,
    ModelFailure,
    ModelReply,
    ModelSuccess,
    Settings,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

HTTP_RATE_LIMITED: Final = 429
HTTP_SERVER_ERROR: Final = 500
HTTP_PAYMENT_ERRORS: Final = (401, 402)


def _messages(system: str, history: Sequence[ChatMessage]) -> list[ChatCompletionMessageParam]:
    messages: list[ChatCompletionMessageParam] = [
        ChatCompletionSystemMessageParam(role="system", content=system)
    ]
    for message in history:
        match message.role:
            case ChatRole.USER:
                messages.append(
                    ChatCompletionUserMessageParam(role="user", content=message.content)
                )
            case ChatRole.ASSISTANT:
                messages.append(
                    ChatCompletionAssistantMessageParam(role="assistant", content=message.content)
                )
            case unreachable:
                assert_never(unreachable)
    return messages


@dataclass(frozen=True, slots=True)
class OpenAITransport:
    """Make one SDK request while preserving the raw response or failure."""

    client: OpenAI
    settings: Settings

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelReply:
        """Return one completion without delegating retry behavior to the SDK."""
        try:
            response = self.client.chat.completions.create(
                model=self.settings.model,
                temperature=self.settings.temperature,
                max_tokens=self.settings.max_tokens,
                messages=_messages(system, history),
            )
        except APITimeoutError:
            return ModelFailure(
                code=ModelError.TIMEOUT,
                detail="request_timeout",
                attempts=1,
                retryable=True,
                stop_batch=False,
            )
        except APIConnectionError:
            return ModelFailure(
                code=ModelError.CONNECTION,
                detail="connection_error",
                attempts=1,
                retryable=True,
                stop_batch=False,
            )
        except APIStatusError as exc:
            retryable = exc.status_code == HTTP_RATE_LIMITED or exc.status_code >= HTTP_SERVER_ERROR
            stop_batch = exc.status_code in HTTP_PAYMENT_ERRORS
            return ModelFailure(
                code=ModelError.API,
                detail=f"HTTP {exc.status_code}",
                attempts=1,
                retryable=retryable,
                stop_batch=stop_batch,
            )

        raw = response.model_dump_json()
        if not response.choices:
            return ModelFailure(
                code=ModelError.EMPTY,
                detail="missing_choices",
                attempts=1,
                retryable=False,
                stop_batch=False,
            )
        text = response.choices[0].message.content or ""
        if not text.strip():
            return ModelFailure(
                code=ModelError.EMPTY,
                detail="empty_content",
                attempts=1,
                retryable=False,
                stop_batch=False,
            )
        tokens = response.usage.total_tokens if response.usage is not None else 0
        return ModelSuccess(text, raw, tokens, 1)


@dataclass(frozen=True, slots=True)
class RetryingModel:
    """Retry only transient failures and report the total number of attempts."""

    transport: ModelClient
    retry_delays_s: tuple[float, ...]
    sleeper: Callable[[float], None]

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelReply:
        """Apply the frozen delay schedule around a single-attempt transport."""
        attempts = 0
        while True:
            reply = self.transport.complete(system, history)
            attempts += reply.attempts
            match reply:
                case ModelSuccess():
                    return replace(reply, attempts=attempts)
                case ModelFailure() if reply.retryable and attempts <= len(self.retry_delays_s):
                    self.sleeper(self.retry_delays_s[attempts - 1])
                case ModelFailure():
                    return replace(reply, attempts=attempts)
                case unreachable:
                    assert_never(unreachable)
