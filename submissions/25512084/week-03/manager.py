from dataclasses import dataclass

from contractor import Contractor, bid
from model import Meter


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def build_team(condition: str):
    if condition == "homogeneous":
        return [
            Contractor("A", "general problem solving"),
            Contractor("B", "general problem solving"),
            Contractor("C", "general problem solving"),
        ]

    if condition == "baseline":
        return [
            Contractor("A", "arithmetic"),
            Contractor("B", "writing"),
            Contractor("C", "coding"),
        ]

    if condition == "overconfident":
        return [
            Contractor("A", "arithmetic"),
            Contractor("B", "writing"),
            Contractor("C", "coding", overconfident=True),
        ]

    raise ValueError(f"unknown condition: {condition}")


def run_round(tasks, team, meter: Meter, log=print):
    result = RoundResult(tasks=len(tasks))

    for task in tasks:
        log(f"[announce] task {task['id']}: {task['desc']}")

        # One announcement sent to each of the three contractors.
        result.messages += len(team)

        bids = []

        for contractor in team:
            parsed, raw = bid(
                contractor,
                task["id"],
                task["desc"],
                meter,
            )

            if parsed is None:
                result.parse_fails += 1
                log(f"[bid] {contractor.name}: PARSE_FAIL raw={raw!r}")
                continue

            log(
                f"[bid] {contractor.name}: "
                f"bid={parsed['bid']} "
                f"confidence={parsed['confidence']} "
                f"reason={parsed['reason']}"
            )

            if parsed["bid"] is True:
                result.messages += 1
                bids.append((parsed["confidence"], contractor))

        if not bids:
            result.unassigned += 1
            log(f"[award] UNASSIGNED (gold {task['gold']})")
            continue

        # Python's sort is stable, so a confidence tie keeps A -> B -> C order.
        bids.sort(key=lambda item: -item[0])
        winner = bids[0][1]

        result.messages += 1

        if winner.name == task["gold"]:
            result.correct += 1
        else:
            result.misawards += 1

        log(f"[award] {winner.name} (gold {task['gold']})")

    return result
