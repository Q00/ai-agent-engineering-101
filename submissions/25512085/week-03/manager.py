"""Deterministic manager for announcement, bid collection, and award."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from contractor import Contractor, ModelCaller
from protocol import Bid, Task, build_announcement


LogFn = Callable[[str], None]


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def select_winner(bids: list[Bid]) -> Bid | None:
    """Select maximum confidence; max() preserves the first bid on a tie."""
    if not bids:
        return None
    return max(bids, key=lambda item: item.confidence)


def run_round(
    tasks: Iterable[Task],
    contractors: list[Contractor],
    call_model: ModelCaller,
    *,
    log: LogFn = print,
) -> RoundResult:
    """Run one complete task set under one experimental condition."""
    result = RoundResult()

    for task in tasks:
        result.tasks += 1
        announcement = build_announcement(task)
        valid_bids: list[Bid] = []

        log(f"[task] id={task.id} desc={task.desc!r}")

        for contractor in contractors:
            # The same announcement is sent separately to each contractor.
            result.messages += 1
            log(
                f"[announcement] to={contractor.name} "
                f"payload={announcement.to_message()}"
            )

            attempt = contractor.request_bid(announcement, call_model)
            log(f"[raw-response] from={contractor.name} value={attempt.raw!r}")

            if attempt.parsed is None:
                result.parse_fails += 1
                log(
                    f"[parse-fail] contractor={contractor.name} "
                    f"reason={attempt.parse_error}"
                )
                continue

            bid = attempt.parsed
            log(
                f"[bid] contractor={bid.contractor} bid={bid.bid} "
                f"confidence={bid.confidence:g} reason={bid.reason!r}"
            )
            if bid.bid:
                result.messages += 1
                valid_bids.append(bid)

        winner = select_winner(valid_bids)
        if winner is None:
            result.unassigned += 1
            log(f"[unassigned] task={task.id}")
            continue

        result.messages += 1
        log(
            f"[award] task={task.id} winner={winner.contractor} "
            f"confidence={winner.confidence:g} gold={task.gold}"
        )

        if winner.contractor == task.gold:
            result.correct += 1
        else:
            result.misawards += 1

    return result
