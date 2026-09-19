"""Week 03 — manager: broadcast, collect bids, award; run a full round over
tasks.json and tally the four metrics from the lab spec (correct, messages,
unassigned, misawards).
"""
import json
from dataclasses import dataclass

from contractor import Contractor, bid
from model import Meter


def collect_bids(team, task_id, desc, meter, log=print):
    """Ask every contractor in turn. Returns (accepted, messages, parse_fails).
    accepted holds only real bid=True offers, as (name, confidence)."""
    accepted = []
    messages = len(team)            # one announcement per contractor (broadcast)
    parse_fails = 0
    for c in team:
        result = bid(c, task_id, desc, meter)
        if result is None:
            parse_fails += 1
            log(f"  [bid] {c.name}: parse failed, treated as no bid")
            continue
        log(f"  [bid] {c.name}: bid={result['bid']} confidence={result['confidence']}")
        if result["bid"] is True:
            accepted.append((c.name, result["confidence"]))
            messages += 1            # one message per actual bid
    return accepted, messages, parse_fails


def award(accepted):
    """Highest confidence wins; a tie goes to whoever appears first (i.e.
    answered first, since contractors are asked in team order)."""
    if not accepted:
        return None                  # unassigned: nobody bid
    best_name, best_confidence = accepted[0]
    for name, confidence in accepted[1:]:
        if confidence > best_confidence:
            best_name, best_confidence = name, confidence
    return best_name


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def run_round(tasks, team, meter, log=print):
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(f"[announce] task {t['id']} -> {[c.name for c in team]}")
        accepted, messages, parse_fails = collect_bids(team, t["id"], t["desc"], meter, log)
        r.messages += messages
        r.parse_fails += parse_fails
        winner = award(accepted)
        if winner is None:
            r.unassigned += 1
            log(f"  [award] none (unassigned, gold {t['gold']})")
            continue
        r.messages += 1              # award message
        if winner == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner} (gold {t['gold']})")
    return r


if __name__ == "__main__":
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)
    team = [
        Contractor(name="A", skill="arithmetic"),
        Contractor(name="B", skill="writing"),
        Contractor(name="C", skill="coding"),
    ]
    m = Meter()
    result = run_round(tasks, team, m)
    print(result)
    print(f"tokens={m.tokens} calls={m.calls}")
