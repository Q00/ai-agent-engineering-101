"""Week 03 — the manager side: announce, collect bids, award.

Message counting follows the week-03 spec: one announcement per contractor,
one per bid, one per award. A task nobody bids on gets no award message.
"""
from dataclasses import dataclass

from contractor import ANNOUNCEMENT, bid


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def _one_line(text: str, limit: int = 160) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def run_round(tasks, team, meter, log=print) -> RoundResult:
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        log(ANNOUNCEMENT.format(cid=t["id"], desc=t["desc"]))
        r.messages += len(team)           # broadcast: one message per contractor
        bids = []
        for c in team:
            parsed, raw = bid(c, t["id"], t["desc"], meter)
            if parsed is None:            # unusable reply = a contractor that did not bid
                r.parse_fails += 1
                log(f"  [bid] {c.name}: PARSE FAIL -> {_one_line(raw)}")
                continue
            log(f"  [bid] {c.name}: bid={parsed['bid']} "
                f"confidence={parsed.get('confidence')} "
                f"reason={_one_line(str(parsed.get('reason')), 100)}")
            if parsed["bid"] is True:
                r.messages += 1           # one message per bid
                bids.append((parsed["confidence"], c))
        if not bids:
            r.unassigned += 1
            log(f"  [award] none — no bid on task {t['id']} (gold {t['gold']})")
            continue
        # highest confidence wins; sort is stable, so a tie goes to whoever
        # answered first, which is the order the team was called in
        bids.sort(key=lambda x: -x[0])
        conf, winner = bids[0]
        r.messages += 1                   # award message
        if winner.name == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner.name} (gold {t['gold']}) confidence={conf}")
    assert r.correct + r.misawards + r.unassigned == r.tasks
    return r
