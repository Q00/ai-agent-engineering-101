"""Two paths the live runs never took, driven by scripted agents.

No model, no key, deterministic. Both the offline suite and the diagram
generator import from here, so the sequence diagram of a path is rendered from
the same scenario the tests assert on — a diagram drawn from a separate
hand-written script could agree with nothing.

WHY THESE TWO EXIST

The graded runs contain 83 announcements and not one REFUSAL, and no subtask
message at all. Neither absence is a protocol failure; both are the runner:

  Concurrency. run_ext.py processes tasks with `for task in tasks`, one
  contract at a time, so no candidate is ever holding one contract while
  another manager awards it a second. Capacity-1, the two-phase award and
  Smith's ACCEPTANCE | REFUSAL pair are implemented and verified, and in the
  measurements they carry no load. "Implemented" and "exercised" are
  different claims and the report says which one this is.

  Depth. At the default read cap a candidate surveys the whole 60-line file in
  three calls, so substituting was always cheaper than handing a piece out.

So these are scenarios, not runs, and every diagram built from them is
labelled that way. The appendix condition ext_tight_reads may yet produce a
real decomposition; if it does, that journal is the better source and this one
should be dropped for it.
"""

import os

import phases as P
import tools as T
from protocol import (ANNOUNCE, BID, AWARD, ACCEPTANCE, REFUSAL, REPORT,
                      Endpoint, Journal)

FIXTURES = os.path.join("diagrams", "fixtures")


# ---------------------------------------------------------------- scripted agent


class Turn:
    def __init__(self, action=None, text="", out_of_role=(), work_calls=()):
        self.action = action
        self.text = text
        self.out_of_role = list(out_of_role)
        self.work_calls = list(work_calls)
        self.disabled = []
        self.steps = 1
        self.stopped = "scripted"


class Fake:
    """Stands in for an Agent. Returns the action a real one would have."""

    def __init__(self, name, script, manifest=None):
        self.name = name
        self.manifest = tuple(manifest if manifest is not None else T.MANIFESTS[name])
        self.trajectory_fn = None
        self.script = script

    def act(self, role, block, round_no=0):
        return self.script(self.name, role, block)


def bid(conf, evidence, reason="scripted"):
    return Turn(("bidding", {"bid": True, "confidence": conf,
                             "evidence": list(evidence), "reason": reason}))


NO_BID = Turn(("bidding", {"bid": False, "confidence": 0, "evidence": [],
                           "reason": "my tools do not cover it"}))


def announce(abstraction="do the work", eligibility="any capable candidate"):
    return Turn(("announce", {"task_abstraction": abstraction,
                              "eligibility": eligibility}))


def award(who, why="best bid"):
    return Turn(("award", {"candidate": who, "reason": why}))


def _net(path, script=None, manifests=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    journal = Journal(path)
    eps = {n: Endpoint(n, T.CANDIDATES, journal=journal) for n in T.CANDIDATES}
    ags = None
    if script is not None:
        man = manifests or T.MANIFESTS
        ags = {n: Fake(n, script, man[n]) for n in T.CANDIDATES}
    return eps, ags, journal


def _deliver(eps, msg):
    if msg is None:
        return None
    for ep in eps.values():
        ep.deliver(msg)
    return msg


# ---------------------------------------------------------------- scenario 1


def concurrency(path=None):
    """Two managers want the same contractor. Driven at the endpoint level,
    because nothing above it can put two contracts in flight at once.

    P1 manages contract 1 and P4 manages contract 2. P2 bids on both, accepts
    1, and is then awarded 2 while still owing 1 — which is exactly the case
    Smith's REFUSAL answers. P4 falls to P3.
    """
    path = path or os.path.join(FIXTURES, "concurrency.jsonl")
    eps, _, journal = _net(path)
    eps["P1"].assign_role("1", "manager")
    eps["P4"].assign_role("2", "manager")

    _deliver(eps, eps["P1"].send(ANNOUNCE, ("P2", "P3", "P4"), "1",
                                 {"text": "contract 1"}, round_no=1))
    _deliver(eps, eps["P4"].send(ANNOUNCE, ("P1", "P2", "P3"), "2",
                                 {"text": "contract 2"}, round_no=1))
    for who in ("P2", "P3"):
        _deliver(eps, eps[who].send(BID, "P1", "1",
                                    {"bid": True, "confidence": 95,
                                     "evidence": list(T.MANIFESTS[who])},
                                    round_no=1))
        _deliver(eps, eps[who].send(BID, "P4", "2",
                                    {"bid": True, "confidence": 90,
                                     "evidence": list(T.MANIFESTS[who])},
                                    round_no=1))
    _deliver(eps, eps["P1"].send(AWARD, "P2", "1", {"reason": "highest"}, round_no=1))
    _deliver(eps, eps["P2"].send(ACCEPTANCE, "P1", "1", {}, round_no=1))

    # P4 awards a candidate that is already committed. Refused, and the
    # manager takes the next bid.
    _deliver(eps, eps["P4"].send(AWARD, "P2", "2", {"reason": "highest"}, round_no=1))
    blocked = eps["P2"].send(ACCEPTANCE, "P4", "2", {}, round_no=1)
    _deliver(eps, eps["P2"].send(REFUSAL, "P4", "2",
                                 {"justification": "committed to 1"}, round_no=1))
    _deliver(eps, eps["P4"].send(AWARD, "P3", "2", {"reason": "next bid"}, round_no=1))
    _deliver(eps, eps["P3"].send(ACCEPTANCE, "P4", "2", {}, round_no=1))
    _deliver(eps, eps["P2"].send(REPORT, "P1", "1", {"answer": "Answer: done"},
                                 round_no=1))
    _deliver(eps, eps["P3"].send(REPORT, "P4", "2", {"answer": "Answer: done"},
                                 round_no=1))

    facts = {
        "acceptance_while_committed_refused": blocked is None,
        "refusals": sum(1 for r in journal.records() if r["type"] == REFUSAL),
        "awards": sum(1 for r in journal.records() if r["type"] == AWARD),
        "held": sum(e.mailbox.pending() for e in eps.values()),
        "committed_after": {n: eps[n].committed for n in T.CANDIDATES},
    }
    journal.close()
    return path, facts


# ---------------------------------------------------------------- scenario 2


def _decomposing_script(name, role, block):
    if "Announce" in block:
        return announce("count the WARN lines", "whoever holds count_level")
    if role == "contractor":
        if "contract 1-6.1" in block:
            return bid(85, ["count_level"]) if name == "P1" else NO_BID
        return bid(70, ["grep_message"]) if name == "P4" else NO_BID
    if "came back" in block:
        return Turn(text="Both halves together.\nAnswer: 11/4")
    if "Award" in block:
        return award("P1") if "6.1" in block else award("P4")
    if "won this contract" in block:
        return announce("count the WARN lines", "whoever holds count_level")
    return Turn(text="Answer: 4")


def depth(path=None):
    """The winner cannot finish alone, hands a piece out, and becomes its
    manager. Task 6 needs count_level + grep_message and nobody holds both.
    """
    path = path or os.path.join(FIXTURES, "depth.jsonl")
    eps, ags, journal = _net(path, _decomposing_script)
    task = {"id": "6", "desc": ("Give two integers separated by a slash: how "
                                "many WARN lines there are in total, and how "
                                "many lines mention 'db connection refused'."),
            "requires": ["count_level", "grep_message"],
            "capable": [], "gold": None, "answer": "11/4"}
    result = P.run_task(task, eps, ags, journal, {}, round_no=1)
    facts = {
        "subtasks": len(result.subtasks),
        "subtask_id": result.subtasks[0].task if result.subtasks else None,
        "subtask_awarded": result.subtasks[0].awarded if result.subtasks else None,
        "solved": result.solved,
        "feasible": result.feasible,
        "depth_messages": sum(1 for r in journal.records() if "." in r["task"]),
        "held": sum(e.mailbox.pending() for e in eps.values()),
    }
    journal.close()
    return path, facts


SCENARIOS = {"concurrency": concurrency, "depth": depth}


def build_all():
    """Regenerate both fixtures. Deterministic, so safe to run every time."""
    return {name: fn() for name, fn in SCENARIOS.items()}


if __name__ == "__main__":
    for name, (path, facts) in sorted(build_all().items()):
        print(f"{name}  ->  {path}")
        for k, v in facts.items():
            print(f"    {k} = {v}")
