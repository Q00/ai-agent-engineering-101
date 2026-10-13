import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import final

from acl_lab.domain import ChatMessage, ModelSuccess, Scenario, Settings
from acl_lab.hybrid_archive import HybridArchive
from acl_lab.hybrid_batch import HybridBatch


@final
class ScriptedModel:
    """Return enough free-form refusals for one complete hybrid batch."""

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
    return ("I refuse.", '{"performative":"refuse","price":null}') * 12


def test_hybrid_batch_writes_twelve_episodes_and_three_logs(tmp_path: Path) -> None:
    archive = HybridArchive(tmp_path)

    completed = HybridBatch(
        archive,
        _settings(),
        _scenarios(),
        ScriptedModel(_replies()),
    ).run()

    with archive.results_path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert completed is True
    assert len(rows) == 12
    assert {row["condition"] for row in rows} == {"hybrid"}
    assert len(tuple(archive.logs.glob("*.txt"))) == 3


def test_hybrid_batch_resume_makes_no_model_calls(tmp_path: Path) -> None:
    archive = HybridArchive(tmp_path)
    HybridBatch(archive, _settings(), _scenarios(), ScriptedModel(_replies())).run()
    model = ScriptedModel(())

    completed = HybridBatch(archive, _settings(), _scenarios(), model).run()

    assert completed is True
    assert model.calls == 0
