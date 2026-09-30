"""Prompts and the three conditions.

Everything a contractor sees lives here. The three conditions differ only in
what `contractors_for` returns: the skill strings, and one extra sentence for
contractor C in `overconfident`. The bid instruction and the announcement
format are the same constants for every condition and every run.

The wording of BID_SYSTEM, OVERCONFIDENT and ANNOUNCEMENT follows the week-03
lecture notes so the numbers stay comparable with the reference run there.
"""
from dataclasses import dataclass

# Common bid instruction. {name} and {skill} are the only holes.
BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

# The one sentence that the `overconfident` condition appends to contractor C.
OVERCONFIDENT = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher."
)

# Task announcement with the four fields of Smith 1980, Fig. 1.
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)

SKILLS = {
    "A": "arithmetic and numeric calculation",
    "B": "writing and rewriting English prose",
    "C": "writing and fixing Python code",
}
GENERALIST = "general problem solving"

CONDITIONS = ("baseline", "homogeneous", "overconfident")


@dataclass(frozen=True)
class ContractorSpec:
    name: str
    skill: str
    extra: str = ""          # appended to the system prompt; empty except in overconfident/C

    @property
    def system(self) -> str:
        return BID_SYSTEM.format(name=self.name, skill=self.skill) + self.extra


def contractors_for(condition: str) -> list[ContractorSpec]:
    """Return the three contractor specs for a condition. Registration order
    (A, B, C) is also the tie-break order in the manager."""
    if condition == "baseline":
        return [ContractorSpec(n, s) for n, s in SKILLS.items()]
    if condition == "homogeneous":
        return [ContractorSpec(n, GENERALIST) for n in SKILLS]
    if condition == "overconfident":
        return [ContractorSpec(n, s, OVERCONFIDENT if n == "C" else "")
                for n, s in SKILLS.items()]
    raise ValueError(f"unknown condition {condition!r}; choose one of {CONDITIONS}")
