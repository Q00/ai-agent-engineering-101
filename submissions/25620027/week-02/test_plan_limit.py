"""Exercise plan limits without making paid model calls."""

import json

import pytest

from harness_plan_execute import run_plan_execute
from tools_shared import Chat, Reply


def script_model(monkeypatch: pytest.MonkeyPatch, replies: list[Reply]) -> None:
    """Replace only the nondeterministic model-call boundary."""
    pending = iter(replies)

    def send(chat: Chat) -> Reply:
        chat.meter.add(1, 1)
        return next(pending, Reply("Answer: 14:00", []))

    monkeypatch.setattr(Chat, "send", send)


@pytest.mark.parametrize("steps", [0, 7, 26])
def test_stops_before_execution_when_initial_plan_exceeds_budget(
    monkeypatch: pytest.MonkeyPatch, steps: int,
) -> None:
    # Given: the model returns an empty or over-budget plan.
    script_model(monkeypatch, [Reply(json.dumps(["count"] * steps), [])])
    events: list[str] = []

    # When: the harness evaluates that plan.
    answer, meter, _ = run_plan_execute("Count errors", log=events.append)

    # Then: no execution or success answer is produced.
    assert answer != "Answer: 14:00"
    assert meter.iters == 1
    assert not any(event.startswith("[step ") for event in events)


def test_executes_all_steps_when_plan_is_at_six_step_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a plan exactly at the selected limit.
    script_model(monkeypatch, [Reply(json.dumps(["count"] * 6), [])])
    events: list[str] = []

    # When: the full plan is run.
    answer, meter, _ = run_plan_execute("Count errors", log=events.append)

    # Then: all six steps and the final answer are retained.
    assert answer == "Answer: 14:00"
    assert meter.iters == 8
    assert sum(event.startswith("[step ") for event in events) == 6


def test_stops_when_replan_exceeds_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one completed step, then six proposed replacement steps.
    script_model(monkeypatch, [
        Reply(json.dumps(["count"] * 6), []),
        Reply("done", []),
        Reply("OFF_PLAN: missing input", []),
        Reply(json.dumps(["retry"] * 6), []),
    ])
    events: list[str] = []

    # When: the replacement plan would make seven total steps.
    answer, meter, replans = run_plan_execute("Count errors", log=events.append)

    # Then: the rejected replan is not executed or truncated.
    assert answer != "Answer: 14:00"
    assert meter.iters == 4
    assert replans == 1
    assert sum(event.startswith("[step ") for event in events) == 2


def test_executes_replan_when_it_fits_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one completed step and five valid replacement steps.
    script_model(monkeypatch, [
        Reply(json.dumps(["count"] * 6), []),
        Reply("done", []),
        Reply("OFF_PLAN: missing input", []),
        Reply(json.dumps(["retry"] * 5), []),
    ])
    events: list[str] = []

    # When: the replacement plan fits the remaining allowance.
    answer, meter, replans = run_plan_execute("Count errors", log=events.append)

    # Then: the full replacement is executed and yields a final answer.
    assert answer == "Answer: 14:00"
    assert meter.iters == 10
    assert replans == 1
    assert sum(event.startswith("[step ") for event in events) == 7
