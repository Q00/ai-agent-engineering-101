"""Award policies of the manager, and the reputation store.

The three required conditions use ConfidencePolicy only. The extra conditions
swap in one of the others while every prompt stays the same, so the only thing
that changes is what the manager looks at when it awards.

  confidence   highest confidence among participating bids; tie -> first registered
  reputation   confidence x reputation weight; weight = (correct wins + 1) / (wins + 2)
  capacity     confidence, but a contractor at its award cap is skipped;
               cap = ceil(tasks / 3) + 1

Reputation needs to know, after each award, whether the winner was the gold
contractor. Smith 1980 has no message for that; it stands in for a post-award
result check the manager would otherwise have to do itself.
"""
import math
from collections import Counter

from protocol import Bid


class ReputationStore:
    """Per-run only. Laplace-smoothed so an unknown contractor starts at 0.5."""

    def __init__(self):
        self.won = Counter()
        self.correct = Counter()

    def record(self, winner, gold):
        if winner is None:
            return
        self.won[winner] += 1
        if winner == gold:
            self.correct[winner] += 1

    def weight(self, name) -> float:
        return (self.correct[name] + 1) / (self.won[name] + 2)


def _pick(scored, log, label):
    """scored: list of (score, Bid) in registration order. Highest score wins,
    an exact tie goes to the earlier one. Returns (winner, tie)."""
    best, tie = None, False
    for score, b in scored:
        if best is None or score > best[0]:
            best, tie = (score, b), False
        elif score == best[0]:
            tie = True
    if best is None:
        return None, False
    if label:
        log(f"  [policy] {label}: " + ", ".join(
            f"{b.contractor} {b.confidence:g}->{s:.1f}" for s, b in scored))
    return best[1].contractor, tie


class ConfidencePolicy:
    name = "confidence"

    def __init__(self, n_tasks: int, store: ReputationStore | None = None):
        self.store = store or ReputationStore()     # always recorded, used only by reputation

    def choose(self, bids: list[Bid], log):
        return _pick([(b.confidence, b) for b in bids], log, "")

    def record(self, task: dict, winner):
        self.store.record(winner, task["gold"])


class ReputationPolicy(ConfidencePolicy):
    name = "reputation"

    def choose(self, bids, log):
        scored = [(b.confidence * self.store.weight(b.contractor), b) for b in bids]
        return _pick(scored, log, "reputation-weighted")


class CapacityPolicy(ConfidencePolicy):
    name = "capacity"

    def __init__(self, n_tasks: int, store=None):
        super().__init__(n_tasks, store)
        self.cap = math.ceil(n_tasks / 3) + 1
        self.awards = Counter()

    def choose(self, bids, log):
        eligible = [b for b in bids if self.awards[b.contractor] < self.cap]
        full = [b.contractor for b in bids if self.awards[b.contractor] >= self.cap]
        if full:
            log(f"  [policy] capacity cap={self.cap}: skipped {', '.join(full)} (at cap)")
        return _pick([(b.confidence, b) for b in eligible], log, "")

    def record(self, task, winner):
        super().record(task, winner)
        if winner is not None:
            self.awards[winner] += 1


POLICIES = {p.name: p for p in (ConfidencePolicy, ReputationPolicy, CapacityPolicy)}
