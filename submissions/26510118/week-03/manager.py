"""Week 03 — the manager: announce, collect bids, award to the highest confidence.

Message counting follows the lecture note: one announcement per contractor, one
message per bid that is actually placed (`bid=true`), one per award. A `bid=false`
reply and an unparseable reply cost no message, so `messages` tracks how much
bidding a condition provokes — which is what makes it a negotiation-cost metric.
Counting every reply instead would pin it at 7 per task and the metric would die.

This module does not know which condition is running. It receives a team and a
task list and measures what happens.
"""
from dataclasses import dataclass

from contractor import bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0      # not a results.csv column — reported in `note`


def run_round(tasks, team, meter, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))

    for t in tasks:
        log(f"[task {t['id']}] {t['desc']}")
        log(f"  [announce] to {', '.join(c.name for c in team)}   (gold {t['gold']})")
        r.messages += len(team)                   # broadcast: one per contractor

        bids = []
        for c in team:
            parsed, raw = bid(c, t["id"], t["desc"], meter)
            if parsed is None:                    # unparseable reply = did not bid
                r.parse_fails += 1
                log(f"  [bid] {c.name}: UNPARSEABLE, counted as no bid "
                    f"-> raw={raw.strip()[:160]!r}")
                continue
            log(f"  [bid] {c.name}: bid={parsed['bid']} "
                f"confidence={parsed['confidence']:g} "
                f"reason={parsed['reason']!r}")
            if parsed["bid"] is True:
                r.messages += 1                   # a placed bid costs one message
                bids.append((parsed["confidence"], c))

        if not bids:
            r.unassigned += 1
            log("  [award] none — no contractor bid")
            continue

        bids.sort(key=lambda x: -x[0])            # highest confidence; stable, so a
        winner = bids[0][1]                       # tie goes to whoever bid first
        r.messages += 1                           # award message
        if winner.name == t["gold"]:
            r.correct += 1
            log(f"  [award] {winner.name} (gold {t['gold']}) -> correct")
        else:
            r.misawards += 1
            log(f"  [award] {winner.name} (gold {t['gold']}) -> MISAWARD")

    log(f"[round] correct={r.correct}/{r.tasks} messages={r.messages} "
        f"unassigned={r.unassigned} misawards={r.misawards} "
        f"parse_fails={r.parse_fails}")
    return r
