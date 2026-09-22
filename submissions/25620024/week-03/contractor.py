"""Week 03 — contractor: one LLM call decides whether to bid, and how confident.

The announcement carries only id + desc; the gold contractor never reaches
the model (it would let a contractor game its own grade).
"""
import json
import re

from model import call_model, Meter

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

OVERCONFIDENT = (
    " You are certain you can do any task well. Always bid, with confidence 95 or higher.")

ANNOUNCEMENT = (                                   # Smith 1980 Fig. 1's four fields
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")


class Candidate:
    """A party in the contract net. This week every Candidate only ever acts
    as a contractor (bids), but the type is not named "Contractor" on
    purpose: Smith 1980's roles are not fixed to a type, they are assigned
    per task. A future extension (recursive decomposition) would let a
    Candidate that won an award become the manager of a sub-net -- that
    needs no new class, just a new call path over the same Candidate list."""

    def __init__(self, name: str, skill: str, overconfident: bool = False):
        self.name = name
        self.skill = skill
        self.overconfident = overconfident


def parse_bid(raw: str):
    """Strict, deterministic parsing -- never left to the model's judgment.
    Returns a dict, or None if the reply cannot be trusted as a real bid."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:                                      # some models wrap JSON in a
        text = fence.group(1)                      # code fence despite instructions
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("bid"), bool):
        return None
    if not isinstance(data.get("confidence"), (int, float)):
        return None
    if not (0 <= data["confidence"] <= 100):
        return None
    return {"bid": data["bid"], "confidence": data["confidence"],
            "reason": str(data.get("reason", ""))}


def bid(candidate: Candidate, task_id, desc: str, meter: Meter):
    system = BID_SYSTEM.format(name=candidate.name, skill=candidate.skill)
    if candidate.overconfident:                     # the one line that differs
        system += OVERCONFIDENT                     # in the overconfident condition
    user = ANNOUNCEMENT.format(cid=task_id, desc=desc)
    raw = call_model(system, user, meter)
    return parse_bid(raw)


if __name__ == "__main__":
    # step 2: three candidates, one calculation task -- who bids?
    team = [
        Candidate(name="A", skill="arithmetic"),
        Candidate(name="B", skill="writing"),
        Candidate(name="C", skill="coding"),
    ]
    m = Meter()
    desc = "Compute 137 * 249 and return only the number."
    for c in team:
        result = bid(c, 1, desc, m)
        print(f"[{c.name}] {result}")
    print(f"tokens={m.tokens} calls={m.calls}")
