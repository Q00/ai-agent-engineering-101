"""Condition name -> (prompt set, award policy, context policy).

The three core conditions are what the assignment requires and what CI reads
from results.csv. The three extra conditions each start from `overconfident`
and change exactly one thing about the market that the overconfident
contractor operates in:

  reputation   the manager weights confidence by past award accuracy
  capacity     the manager caps how many tasks one contractor may win
  memory       contractors remember their own bids and who was awarded

Extra conditions are recorded in results-extra.csv (same header) so that
results.csv keeps only the condition names CI accepts.
"""
from dataclasses import dataclass

from policies import CapacityPolicy, ConfidencePolicy, ReputationPolicy


@dataclass(frozen=True)
class Condition:
    name: str
    prompts: str          # which contractor set: baseline | homogeneous | overconfident
    policy: type          # award policy class, constructed with n_tasks
    context: str          # fresh | memory
    core: bool            # True -> results.csv, False -> results-extra.csv


CONDITIONS = {c.name: c for c in (
    Condition("baseline",      "baseline",      ConfidencePolicy, "fresh",  True),
    Condition("homogeneous",   "homogeneous",   ConfidencePolicy, "fresh",  True),
    Condition("overconfident", "overconfident", ConfidencePolicy, "fresh",  True),
    Condition("reputation",    "overconfident", ReputationPolicy, "fresh",  False),
    Condition("capacity",      "overconfident", CapacityPolicy,   "fresh",  False),
    Condition("memory",        "overconfident", ConfidencePolicy, "memory", False),
)}
