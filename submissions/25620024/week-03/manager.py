"""Week 03 — manager: broadcast, collect bids, award; run a full round over
tasks.json and tally the four metrics from the lab spec (correct, messages,
unassigned, misawards).
"""
import json
from dataclasses import dataclass

from contractor import Candidate, bid
from model import Meter


class Reputation:
    """Axis 3: confidence is judged, not computed (today's lecture problem).
    This tracks each candidate's actual track record and lets the manager
    weight raw confidence by it, plus remembers a task once it has been
    correctly awarded so the SAME task never needs a fresh announce/bid in
    a later rep (Smith 1980's DIRECTED-AWARD: skip negotiation once the
    manager already knows who is right for a job).

    Never exposed to the model: only run_round() reads or writes this,
    using the gold label the candidates themselves never see.
    """

    def __init__(self):
        self.correct = {}   # name -> count
        self.total = {}      # name -> count
        self.solved = {}     # task_id -> name, set only once correctly awarded

    def score(self, name):
        """Track record as a multiplier; 1.0 (neutral) with no history yet
        so the first rep of a condition behaves exactly like plain baseline."""
        total = self.total.get(name, 0)
        return 1.0 if total == 0 else self.correct.get(name, 0) / total

    def record(self, name, was_correct, task_id):
        self.total[name] = self.total.get(name, 0) + 1
        if was_correct:
            self.correct[name] = self.correct.get(name, 0) + 1
            self.solved[task_id] = name

    def directed_winner(self, task_id):
        return self.solved.get(task_id)  # None if never solved correctly before


def collect_bids(team, task_id, desc, meter, log=print):
    """Ask every candidate in turn. Returns (accepted, messages, parse_fails).
    accepted holds only real bid=True offers, as (name, confidence)."""
    accepted = []
    messages = len(team)            # one announcement per candidate (broadcast)
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


def award(accepted, reputation=None):
    """Highest (reputation-weighted) confidence wins; a tie goes to whoever
    appears first (i.e. answered first, since candidates are asked in team
    order). With no reputation object, this is unweighted -- baseline,
    homogeneous and overconfident are untouched by axis 3."""
    if not accepted:
        return None                  # unassigned: nobody bid
    def weighted(name, confidence):
        return confidence if reputation is None else confidence * reputation.score(name)
    best_name, best_score = accepted[0][0], weighted(*accepted[0])
    for name, confidence in accepted[1:]:
        score = weighted(name, confidence)
        if score > best_score:
            best_name, best_score = name, score
    return best_name


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def run_round(tasks, team, meter, log=print, reputation=None):
    r = RoundResult(tasks=len(tasks))
    for t in tasks:
        directed = reputation.directed_winner(t["id"]) if reputation else None
        if directed is not None:
            r.messages += 1          # directed award: announce/bid skipped entirely
            r.correct += 1           # a directed award only ever repeats a proven-correct outcome
            log(f"[directed-award] task {t['id']} -> {directed} "
                f"(reputation: announce/bid skipped)")
            continue
        log(f"[announce] task {t['id']} -> {[c.name for c in team]}")
        accepted, messages, parse_fails = collect_bids(team, t["id"], t["desc"], meter, log)
        r.messages += messages
        r.parse_fails += parse_fails
        winner = award(accepted, reputation)
        if winner is None:
            r.unassigned += 1
            log(f"  [award] none (unassigned, gold {t['gold']})")
            continue
        r.messages += 1              # award message
        was_correct = winner == t["gold"]
        if was_correct:
            r.correct += 1
        else:
            r.misawards += 1
        if reputation is not None:
            reputation.record(winner, was_correct, t["id"])
        log(f"  [award] {winner} (gold {t['gold']})")
    return r


if __name__ == "__main__":
    with open("tasks.json", encoding="utf-8") as f:
        tasks = json.load(f)
    team = [
        Candidate(name="A", skill="arithmetic"),
        Candidate(name="B", skill="writing"),
        Candidate(name="C", skill="coding"),
    ]
    m = Meter()
    result = run_round(tasks, team, m)
    print(result)
    print(f"tokens={m.tokens} calls={m.calls}")
