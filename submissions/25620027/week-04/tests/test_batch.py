import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import final

from acl_lab.archive import ResultArchive
from acl_lab.batch import Batch
from acl_lab.domain import ChatMessage, ModelSuccess, Scenario, Settings


@final
class ScriptedModel:
    """Return the precomputed messages for one complete offline batch."""

    def __init__(self, replies: Iterable[str]) -> None:
        self._replies: Iterator[str] = iter(replies)
        self.calls: int = 0

    def complete(self, system: str, history: tuple[ChatMessage, ...]) -> ModelSuccess:
        del system, history
        self.calls += 1
        return ModelSuccess(text=next(self._replies), raw="{}", tokens=1, attempts=1)


def _settings() -> Settings:
    return Settings(
        provider="test",
        base_url="https://example.invalid/v1",
        model="fake",
        temperature=0,
        max_tokens=64,
        request_timeout_s=10,
        max_turns=8,
        repetitions=3,
        retry_delays_s=(1,),
        prompt_version="test-v1",
    )


def _scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(id=1, item="a", reserve=10, budget=20),
        Scenario(id=2, item="b", reserve=20, budget=20),
        Scenario(id=3, item="c", reserve=30, budget=20),
        Scenario(id=4, item="d", reserve=40, budget=10),
    )


def _replies() -> tuple[str, ...]:
    free = ("I refuse.", '{"performative":"refuse","price":null}') * 12
    tagged = ("(refuse) I am leaving.",) * 12
    structured = ('{"performative":"refuse","content":{"price":null}}',) * 12
    return free + tagged + structured


def test_batch_writes_36_episodes_and_nine_run_logs(tmp_path: Path) -> None:
    # Given
    archive = ResultArchive(tmp_path)
    model = ScriptedModel(_replies())
    batch = Batch(archive, _settings(), _scenarios(), model)

    # When
    completed = batch.run()

    # Then
    with archive.results_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert completed is True
    assert len(rows) == 36
    assert len(tuple(archive.logs.glob("*.txt"))) == 9


def test_batch_resume_makes_no_model_calls_when_all_keys_exist(tmp_path: Path) -> None:
    # Given
    archive = ResultArchive(tmp_path)
    Batch(archive, _settings(), _scenarios(), ScriptedModel(_replies())).run()
    model = ScriptedModel(())

    # When
    completed = Batch(archive, _settings(), _scenarios(), model).run()

    # Then
    assert completed is True
    assert model.calls == 0
