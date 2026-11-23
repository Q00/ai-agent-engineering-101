"""The manager: broadcast an announcement, collect bids, award on confidence.

One round = every task in tasks.json handled once.

Message counting follows the assignment's rule, not Smith's grammar, and the
two differ. The assignment counts one announcement per contractor, one
message per bid received, and one award. Smith 1980 also has REFUSAL as a
message type, so a contractor that answers "I will not bid" has sent
something; here that costs nothing. Unparseable replies likewise cost
nothing. The rule is kept as specified so these numbers can be compared with
the reference run, and the gap is a row in the report's comparison table
rather than a silent adjustment.

What the manager does NOT do, and both absences are findings rather than
omissions: it does not verify that a bid is true, and it has no human
intervention point. Smith's protocol has no message for either, which is the
third limit from the lecture — a bid is a claim, and nothing in the procedure
checks it.
"""

import json
from dataclasses import dataclass, field

from contractor import bid, build_team
from model import Meter, run_header


@dataclass
class RoundResult:
    """One round's metrics. Maps onto the results.csv header."""

    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    # Not columns of their own; they go in the note field.
    parse_fails: int = 0
    how_counts: dict = field(default_factory=dict)
    awards: list = field(default_factory=list)

    def note(self, extra: str = "") -> str:
        """The results.csv note column: everything that is not its own column."""
        paths = " ".join(f"{k}={v}" for k, v in sorted(self.how_counts.items()))
        parts = [f"parse_fails={self.parse_fails}"]
        if paths:
            parts.append(paths)
        if extra:
            parts.append(extra)
        return " ".join(parts)


def run_round(tasks, team, meter: Meter, log=print) -> RoundResult:
    """Announce every task to every contractor, award each to the highest bid."""
    r = RoundResult(tasks=len(tasks))

    for t in tasks:
        cid, desc, gold = t["id"], t["desc"], t["gold"]
        log(f"[announce] contract {cid} to {len(team)} contractors "
            f"(gold {gold}): {desc}")
        r.messages += len(team)          # broadcast: one per contractor

        bids = []
        for c in team:
            parsed, how, raw = bid(c, cid, desc, meter)
            r.how_counts[how] = r.how_counts.get(how, 0) + 1
            if parsed is None:
                # Counted as "did not bid", per the assignment. The raw reply
                # is logged because a failure with no record of its cause
                # cannot be written up.
                r.parse_fails += 1
                log(f"  [no-bid] {c.name}: unparseable ({how}) raw={raw!r}")
                continue
            log(f"  [bid] {c.name}: bid={parsed['bid']} "
                f"confidence={parsed['confidence']:g} how={how} "
                f"reason={parsed['reason']!r}")
            if parsed["bid"] is True:
                r.messages += 1          # one bid received = one message
                bids.append((parsed["confidence"], c))

        if not bids:
            r.unassigned += 1
            log(f"  [unassigned] contract {cid}: no bids")
            r.awards.append({"id": cid, "gold": gold, "winner": None,
                             "confidence": None, "outcome": "unassigned"})
            continue

        # Highest confidence wins. sorted() is stable, so equal confidences
        # keep the order the contractors were asked in — which is the
        # assignment's "whoever answered first". Worth naming: that only holds
        # because this manager asks sequentially. Smith's nodes bid
        # concurrently, where arrival order is a property of the network, not
        # of a loop.
        bids = sorted(bids, key=lambda b: -b[0])
        conf, winner = bids[0]
        r.messages += 1                  # award
        outcome = "correct" if winner.name == gold else "misaward"
        if outcome == "correct":
            r.correct += 1
        else:
            r.misawards += 1
        log(f"  [award] {winner.name} at {conf:g} (gold {gold}) -> {outcome}")
        r.awards.append({"id": cid, "gold": gold, "winner": winner.name,
                         "confidence": conf, "outcome": outcome})

    return r


def load_tasks(path: str = "tasks.json"):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    # One round for one condition, printed. No results.csv, no logs/ — those
    # belong to the runner in step 5. This is here to check the loop.
    import sys

    condition = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    tasks = load_tasks()
    team = build_team(condition)
    meter = Meter()

    print(run_header())
    print(f"condition={condition} tasks={len(tasks)} "
          f"team={[f'{c.name}:{c.skill}' for c in team]}")
    print("-" * 72)
    r = run_round(tasks, team, meter)
    print("-" * 72)
    print(f"tasks={r.tasks} correct={r.correct} messages={r.messages} "
          f"unassigned={r.unassigned} misawards={r.misawards}")
    print(f"note: {r.note(f'tokens={meter.tokens} calls={meter.calls}')}")
