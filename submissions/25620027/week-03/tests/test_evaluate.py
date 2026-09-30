from __future__ import annotations

from cnp.domain import ContractId, Task, TaskId, runtime_task
from cnp.evaluate import evaluate
from cnp.records import Contract, Event


def test_scoring_counts_wrong_award_separately_from_unassigned() -> None:
    # Given one wrong award and one task with no award.
    tasks = (
        Task(id=TaskId("T1"), desc="math", gold="A"),
        Task(id=TaskId("T2"), desc="write", gold="B"),
    )
    contracts = (
        Contract(id=ContractId("1:T1"), task=runtime_task(tasks[0]), phase="CLOSED", winner="C"),
        Contract(id=ContractId("1:T2"), task=runtime_task(tasks[1]), phase="CLOSED"),
    )
    events = (
        Event(stream="protocol_messages", contract_id=contracts[0].id, kind="AWARD"),
        Event(stream="system_events", contract_id=contracts[0].id, kind="REJECT"),
    )
    # When evaluating, then diagnostics cannot inflate messages.
    metrics = evaluate(contracts, tasks, events)
    assert (
        metrics.tasks,
        metrics.correct,
        metrics.unassigned,
        metrics.misawards,
        metrics.messages,
    ) == (2, 0, 1, 1, 1)
