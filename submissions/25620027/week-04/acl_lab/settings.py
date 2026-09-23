"""Boundary parsing for the frozen configuration and scenario set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from pydantic import TypeAdapter, ValidationError

from acl_lab.domain import Scenario, Settings

if TYPE_CHECKING:
    from pathlib import Path

MIN_SCENARIOS: Final = 4
REQUIRED_MAX_TURNS: Final = 8
SCENARIOS: Final = TypeAdapter(tuple[Scenario, ...])


@dataclass(frozen=True, slots=True)
class InputError(Exception):
    """Configuration or preregistration failed before any model call."""

    code: str

    @override
    def __str__(self) -> str:
        return self.code


def load_inputs(
    root: Path, config_path: Path | None = None
) -> tuple[Settings, tuple[Scenario, ...]]:
    """Parse committed inputs once and reject invalid experimental classes."""
    try:
        settings = Settings.model_validate_json((config_path or root / "config.json").read_bytes())
        scenarios = SCENARIOS.validate_json((root / "scenarios.json").read_bytes())
    except (OSError, ValidationError) as exc:
        raise InputError(code="invalid_input_file") from exc

    ids = {str(scenario.id) for scenario in scenarios}
    if len(scenarios) < MIN_SCENARIOS:
        raise InputError(code="too_few_scenarios")
    if len(ids) != len(scenarios):
        raise InputError(code="duplicate_scenario_id")
    possible = sum(scenario.deal_possible for scenario in scenarios)
    if possible in (0, len(scenarios)):
        raise InputError(code="scenario_classes_missing")
    if settings.max_turns != REQUIRED_MAX_TURNS:
        raise InputError(code="max_turns_must_be_eight")
    return settings, scenarios
