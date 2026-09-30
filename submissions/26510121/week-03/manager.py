"""The manager: announce to everyone, collect bids, award to the highest
confidence. One pass over the whole task list is one round.

Message counting follows the assignment: one announcement per contractor,
one message per bid that arrives, one award message per awarded task. A task
that draws no bid produces no award message.

The award rule is Smith's plus the one thing Smith left open (what a bid is
worth): here the bid price is the model's own verbalized confidence, and the
manager has nothing to check it against. That is the whole point of the
experiment, so this file deliberately has no reputation and no verification.
`bond_net.py` is where those are added.
"""
from dataclasses import dataclass, field

from contractor import ANNOUNCEMENT, parse_bid, system_prompt


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    recovered: int = 0          # parse failures that were JSON inside prose or a fence
    records: list = field(default_factory=list)   # one row per contractor per task

    def note(self, extra: str = "") -> str:
        parts = [f"parse_fails={self.parse_fails}",
                 f"fence_or_prose={self.recovered}"]
        if extra:
            parts.append(extra)
        return " ".join(parts)


def run_round(tasks, team, meter, ask, condition, run_no, log=print):
    """One round. `ask(system, user, meter) -> str` is injected so the counting
    can be exercised without a provider."""
    r = RoundResult(tasks=len(tasks))

    for t in tasks:
        announcement = ANNOUNCEMENT.format(cid=t["id"], desc=t["desc"])
        r.messages += len(team)               # broadcast: one per contractor
        log(f"\n[announce] contract {t['id']} -> {', '.join(c.name for c in team)}")
        log(f"  task-abstraction: {t['desc']}")

        bids = []
        for c in team:
            raw = ask(system_prompt(c), announcement, meter)
            parsed, recovered = parse_bid(raw)
            rec = {"condition": condition, "run": run_no, "task": t["id"],
                   "gold": t["gold"], "contractor": c.name,
                   "overconfident": c.overconfident,
                   "parse_ok": parsed is not None, "recovered": recovered,
                   "bid": None, "confidence": None, "reason": None,
                   "raw": (raw or "").strip()[:500]}
            if parsed is None:
                r.parse_fails += 1
                r.recovered += int(recovered)
                tag = "unparseable (JSON inside prose/fence)" if recovered else "unparseable"
                log(f"  [no-bid] {c.name}: {tag}: "
                    f"{(raw or '').strip()[:120].replace(chr(10), ' | ')}")
                r.records.append(rec)
                continue

            rec.update(bid=parsed["bid"], confidence=parsed["confidence"],
                       reason=parsed["reason"])
            r.records.append(rec)
            if parsed["bid"]:
                r.messages += 1               # a bid that arrives is one message
                bids.append((parsed["confidence"], c))
                log(f"  [bid] {c.name}: bid=True confidence={parsed['confidence']:.0f} "
                    f"reason={parsed['reason']}")
            else:
                log(f"  [refuse] {c.name}: bid=False reason={parsed['reason']}")

        if not bids:
            r.unassigned += 1
            log(f"  [unassigned] contract {t['id']} drew no bid (gold {t['gold']})")
            continue

        # highest confidence wins; a tie goes to whoever answered first, and
        # the team is polled in order, so a stable sort is exactly that rule
        bids.sort(key=lambda x: -x[0])
        winner = bids[0][1]
        r.messages += 1                       # the award
        if winner.name == t["gold"]:
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner.name} at confidence {bids[0][0]:.0f} "
            f"(gold {t['gold']}) -> {'correct' if winner.name == t['gold'] else 'MISAWARD'}")

    return r
