"""Select the immutable scenario, condition, and host-policy experiment matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from market.models import Condition, HostPolicy, Scenario

REQUIRED_CONDITIONS: Final = (Condition.PROMPT_INJECT, Condition.SERVER_INJECT)
ALL_CONDITIONS: Final = (
    Condition.PROMPT,
    Condition.SERVER,
    Condition.PROMPT_INJECT,
    Condition.SERVER_INJECT,
)


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    """One fixed matrix and the host policy used throughout it."""

    scenarios: tuple[Scenario, ...]
    conditions: tuple[Condition, ...]
    host_policy: HostPolicy


def select_experiment_plan(
    scenarios: tuple[Scenario, ...],
    *,
    all_conditions: bool,
    liveness_extension: bool,
) -> ExperimentPlan:
    """Keep the required study unchanged and isolate the liveness follow-up."""
    if liveness_extension and all_conditions:
        message = "liveness extension uses only the two required injection conditions"
        raise ValueError(message)
    if liveness_extension:
        possible = tuple(
            scenario for scenario in scenarios if scenario.reserve <= scenario.budget
        )
        return ExperimentPlan(
            scenarios=possible,
            conditions=REQUIRED_CONDITIONS,
            host_policy=HostPolicy.CLOSURE_AWARE,
        )
    conditions = ALL_CONDITIONS if all_conditions else REQUIRED_CONDITIONS
    return ExperimentPlan(
        scenarios=scenarios,
        conditions=conditions,
        host_policy=HostPolicy.BASELINE,
    )
