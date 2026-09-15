"""Boundary parsing for reproducible run configuration and committed inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from pydantic import Field, TypeAdapter

from cnp.domain import FrozenModel, Task, TaskId, Text

if TYPE_CHECKING:
    from pathlib import Path


class Settings(FrozenModel):
    """Common settings; condition is the only prompt intervention."""

    provider: Text
    base_url: Text
    model: Text
    temperature: Annotated[float, Field(ge=0, le=2)]
    max_tokens: Annotated[int, Field(gt=0)]
    request_timeout_s: Annotated[float, Field(gt=0, le=60)]
    contract_deadline_s: Annotated[float, Field(gt=0)]
    min_call_interval_s: Annotated[float, Field(ge=0)]
    prompt_version: Text
    task_order: tuple[TaskId, ...]


MIN_TASKS: Final = 5
MIN_GOLDS: Final = 2
TASKS: Final = TypeAdapter(tuple[Task, ...])


class InputError(Exception):
    """An invalid dataset/configuration must fail before any network call."""

    def __init__(self, code: str) -> None:
        self.code: str = code
        super().__init__(code)


def load_inputs(root: Path) -> tuple[Settings, tuple[Task, ...]]:
    """Require a unique committed task set and a permutation execution order."""
    settings = Settings.model_validate_json((root / "config.json").read_bytes())
    tasks = TASKS.validate_json((root / "tasks.json").read_bytes())
    ids = {t.id for t in tasks}
    if len(tasks) < MIN_TASKS or len(ids) != len(tasks) or len({t.gold for t in tasks}) < MIN_GOLDS:
        msg = "invalid_task_set"
        raise InputError(msg)
    if set(settings.task_order) != ids or len(settings.task_order) != len(tasks):
        msg = "invalid_task_order"
        raise InputError(msg)
    if settings.contract_deadline_s < 3 * (
        settings.request_timeout_s + settings.min_call_interval_s
    ):
        msg = "deadline_too_short_for_sequential_calls"
        raise InputError(msg)
    by_id = {t.id: t for t in tasks}
    return settings, tuple(by_id[i] for i in settings.task_order)
