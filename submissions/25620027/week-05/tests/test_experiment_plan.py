import pytest

from market.experiment_plan import select_experiment_plan
from market.models import Condition, HostPolicy, Scenario


@pytest.fixture
def scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(id=1, item="possible-a", reserve=100, budget=120),
        Scenario(id=2, item="possible-b", reserve=90, budget=90),
        Scenario(id=3, item="impossible", reserve=130, budget=110),
    )


def test_liveness_plan_uses_only_possible_scenarios(
    scenarios: tuple[Scenario, ...],
) -> None:
    plan = select_experiment_plan(
        scenarios,
        all_conditions=False,
        liveness_extension=True,
    )

    assert tuple(scenario.id for scenario in plan.scenarios) == (1, 2)
    assert plan.conditions == (
        Condition.PROMPT_INJECT,
        Condition.SERVER_INJECT,
    )
    assert plan.host_policy is HostPolicy.CLOSURE_AWARE


def test_required_plan_remains_the_baseline_matrix(
    scenarios: tuple[Scenario, ...],
) -> None:
    plan = select_experiment_plan(
        scenarios,
        all_conditions=False,
        liveness_extension=False,
    )

    assert plan.scenarios == scenarios
    assert len(plan.conditions) == 2
    assert plan.host_policy is HostPolicy.BASELINE


def test_liveness_plan_rejects_unrelated_control_conditions(
    scenarios: tuple[Scenario, ...],
) -> None:
    with pytest.raises(ValueError, match="liveness extension"):
        select_experiment_plan(
            scenarios,
            all_conditions=True,
            liveness_extension=True,
        )
