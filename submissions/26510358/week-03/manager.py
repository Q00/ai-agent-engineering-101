"""Sequential announce/bid/award loop with the course's message accounting."""
from dataclasses import dataclass

from contractor import ANNOUNCEMENT, bid


@dataclass
class RoundResult:
    tasks: int
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    completed_tasks: int = 0


def run_round(tasks, team, meter, call_model, log, result=None):
    result = result if result is not None else RoundResult(tasks=len(tasks))
    for task in tasks:
        bids = []
        for contractor in team:
            result.messages += 1
            log("announce", task=task["id"], contractor=contractor.name,
                content=ANNOUNCEMENT.format(cid=task["id"], desc=task["desc"]))
            reply = bid(contractor, task["id"], task["desc"], meter, call_model, log)
            if reply is None:
                result.parse_fails += 1
            elif reply["bid"] is True:
                result.messages += 1
                bids.append((reply["confidence"], contractor.name))
        if not bids:
            result.unassigned += 1
            log("unassigned", task=task["id"], gold=task["gold"])
        else:
            # max() returns the first maximum; names must never break ties.
            confidence, winner = max(bids, key=lambda item: item[0])
            result.messages += 1
            if winner == task["gold"]:
                result.correct += 1
            else:
                result.misawards += 1
            log("award", task=task["id"], winner=winner, confidence=confidence,
                gold=task["gold"], correct=winner == task["gold"])
        result.completed_tasks += 1
    return result
