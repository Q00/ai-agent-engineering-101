from collections.abc import Callable, Iterable, Iterator
from typing import final

from acl_lab.adapter import RetryingModel
from acl_lab.domain import (
    ChatMessage,
    ModelError,
    ModelFailure,
    ModelReply,
    ModelSuccess,
)


@final
class ScriptedTransport:
    """Return a fixed transport result sequence without network access."""

    def __init__(self, replies: Iterable[ModelReply]) -> None:
        self._replies: Iterator[ModelReply] = iter(replies)

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelReply:
        del system, history
        return next(self._replies)


def test_retrying_model_waits_and_returns_success_after_rate_limit() -> None:
    # Given
    transport = ScriptedTransport(
        (
            ModelFailure(ModelError.API, "HTTP 429", 1, retryable=True, stop_batch=False),
            ModelSuccess("ok", "{}", 2, 1),
        )
    )
    waits: list[float] = []
    sleeper: Callable[[float], None] = waits.append
    model = RetryingModel(transport, (5.0, 10.0), sleeper)

    # When
    result = model.complete("system", ())

    # Then
    assert result == ModelSuccess("ok", "{}", 2, 2)
    assert waits == [5.0]


def test_retrying_model_preserves_failure_after_retry_budget() -> None:
    # Given
    failure = ModelFailure(ModelError.TIMEOUT, "timeout", 1, retryable=True, stop_batch=False)
    transport = ScriptedTransport((failure, failure))
    model = RetryingModel(transport, (1.0,), lambda _: None)

    # When
    result = model.complete("system", ())

    # Then
    assert result == ModelFailure(
        ModelError.TIMEOUT,
        "timeout",
        2,
        retryable=True,
        stop_batch=False,
    )
