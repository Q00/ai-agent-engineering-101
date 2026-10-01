from collections.abc import Iterable, Iterator
from typing import final

from acl_lab.domain import (
    ChatMessage,
    Condition,
    EpisodeContext,
    ModelSuccess,
    Outcome,
    Scenario,
)
from acl_lab.engine import run_episode


@final
class ScriptedModel:
    """Return fixed model messages so the full episode loop stays offline."""

    def __init__(self, replies: Iterable[str]) -> None:
        self._replies: Iterator[str] = iter(replies)

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelSuccess:
        del system, history
        return ModelSuccess(text=next(self._replies), raw="{}", tokens=1, attempts=1)


def test_run_episode_records_structured_deal_without_reader_calls() -> None:
    # Given
    context = EpisodeContext(
        scenario=Scenario(id=1, item="monitor", reserve=120, budget=150),
        condition=Condition.STRUCTURED,
        max_turns=8,
    )
    model = ScriptedModel(
        (
            '{"performative":"propose","content":{"price":130}}',
            '{"performative":"accept-proposal","content":{"price":null}}',
        )
    )

    # When
    execution = run_episode(context, model)

    # Then
    assert execution.result.outcome is Outcome.DEAL
    assert execution.result.price == 130
    assert execution.result.correct == 1
    assert execution.result.violation == 0
    assert execution.result.turns == 2
    assert execution.result.reader_calls == 0


def test_run_episode_counts_free_reader_label_and_no_deal() -> None:
    # Given
    context = EpisodeContext(
        scenario=Scenario(id=3, item="bicycle", reserve=120, budget=100),
        condition=Condition.FREE,
        max_turns=8,
    )
    model = ScriptedModel(("What is your asking price?", '{"performative":"refuse","price":null}'))

    # When
    execution = run_episode(context, model)

    # Then
    assert execution.result.outcome is Outcome.NO_DEAL
    assert execution.result.correct == 1
    assert execution.result.reader_calls == 1
    assert execution.result.turns == 1
