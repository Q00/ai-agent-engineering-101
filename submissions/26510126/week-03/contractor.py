"""One contractor = one system prompt + one model call per announcement.

The announcement follows the four fields of Smith 1980 Fig. 1:
task-abstraction, eligibility-specification, bid-specification,
expiration-time. Keeping the paper's field names means the comparison table
the report has to produce is a comparison of the same slots, not of two
different formats.

The independent variable lives in exactly two places in this file: the
`skill` string a contractor is built with, and the one sentence appended to
its system prompt when `overconfident` is set. Nothing else differs between
the three conditions.
"""

import json
import re
from dataclasses import dataclass

from model import Meter, call_model

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

OVERCONFIDENT = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher.")

ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")

# The three conditions. Only `skill` and `overconfident` move.
SKILLS = {
    "baseline": {"A": "arithmetic and numeric computation",
                 "B": "writing and rewriting prose",
                 "C": "writing and fixing code"},
    "homogeneous": {"A": "general problem solving",
                    "B": "general problem solving",
                    "C": "general problem solving"},
    "overconfident": {"A": "arithmetic and numeric computation",
                      "B": "writing and rewriting prose",
                      "C": "writing and fixing code"},
}
CONDITIONS = tuple(SKILLS)


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    def system_prompt(self) -> str:
        system = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:
            system += OVERCONFIDENT
        return system


def build_team(condition: str, order: str = "forward") -> list:
    """Three contractors for one condition. The only per-condition branch.

    `order` is not a condition and is not one of the three the assignment
    defines. It exists because the award rule's tie-break — highest
    confidence, and whoever answered first when equal — resolves to "whoever
    was asked first" only because the manager asks sequentially. When every
    bid comes in at the same number, the winner is then decided by this list's
    order rather than by anything the contractors said.

    Reversing it measures how much of a result came from that. Default is
    "forward", so every graded run is unaffected.
    """
    if condition not in SKILLS:
        raise ValueError(f"condition must be one of {CONDITIONS}, got {condition!r}")
    if order not in ("forward", "reverse"):
        raise ValueError(f"order must be forward or reverse, got {order!r}")
    skills = SKILLS[condition]
    names = ("A", "B", "C") if order == "forward" else ("C", "B", "A")
    return [Contractor(name=n, skill=skills[n],
                       overconfident=(condition == "overconfident" and n == "C"))
            for n in names]


_OBJ = re.compile(r"\{.*\}", re.S)


def parse_bid(raw: str):
    """Parse a bid reply. Returns (bid dict or None, how).

    `how` records which path the reply took, because the difference between
    "answered in clean JSON" and "answered in prose with JSON buried in it"
    is a measurement, not a detail. The lab counts unparseable replies, and a
    parser lenient enough to rescue everything would erase the very failure
    mode the overconfident condition is expected to produce.

    Paths, tightest first:
      ok        the whole reply is the JSON object
      fenced    the reply is one ```...``` block around the object
      embedded  an object was found inside surrounding prose
      not_json  no object could be parsed
      bad_shape an object parsed but bid/confidence are not usable

    Only `not_json` and `bad_shape` are failures. All three successful paths
    yield a bid; the report distinguishes them so "C stopped answering in
    JSON" can be stated with a number rather than an impression.
    """
    if not raw or not raw.strip():
        return None, "not_json"
    text = raw.strip()

    how = "ok"
    obj = None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", text,
                          flags=re.I | re.S).strip()
        if stripped != text:
            try:
                obj = json.loads(stripped)
                how = "fenced"
            except json.JSONDecodeError:
                obj = None
        if obj is None:
            m = _OBJ.search(text)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    how = "embedded"
                except json.JSONDecodeError:
                    return None, "not_json"
            else:
                return None, "not_json"

    if not isinstance(obj, dict) or "bid" not in obj:
        return None, "bad_shape"

    bid = obj["bid"]
    if isinstance(bid, str):
        low = bid.strip().lower()
        if low in ("true", "yes"):
            bid = True
        elif low in ("false", "no"):
            bid = False
        else:
            return None, "bad_shape"
    if not isinstance(bid, bool):
        return None, "bad_shape"

    conf = obj.get("confidence", 0)
    if isinstance(conf, str):
        try:
            conf = float(conf.strip().rstrip("%"))
        except ValueError:
            return None, "bad_shape"
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        return None, "bad_shape"
    conf = float(conf)
    if not 0 <= conf <= 100:
        return None, "bad_shape"
    if not bid:
        conf = 0.0

    reason = obj.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)

    return {"bid": bid, "confidence": conf, "reason": reason.strip()}, how


def bid(contractor: Contractor, cid, desc: str, meter: Meter):
    """Send one announcement to one contractor. Returns (bid or None, how, raw).

    The raw reply is returned so the caller can log what the model actually
    said, including when it could not be parsed. A failure with no record of
    what caused it cannot be written up.
    """
    system = contractor.system_prompt()
    user = ANNOUNCEMENT.format(cid=cid, desc=desc)
    raw = call_model(system, user, meter)
    parsed, how = parse_bid(raw)
    return parsed, how, raw


if __name__ == "__main__":
    # Smoke test: one contractor, one announcement, no manager yet.
    import sys
    from model import run_header

    cond = sys.argv[1] if len(sys.argv) > 1 else "baseline"
    tasks = json.load(open("tasks.json", encoding="utf-8"))
    task = tasks[0]

    print(run_header())
    print(f"condition={cond} task={task['id']} gold={task['gold']}")
    print(f"  desc: {task['desc']}")
    meter = Meter()
    for c in build_team(cond):
        parsed, how, raw = bid(c, task["id"], task["desc"], meter)
        if parsed is None:
            print(f"  [no-bid] {c.name}: unparseable ({how}) raw={raw!r}")
        else:
            print(f"  [bid] {c.name}: bid={parsed['bid']} "
                  f"confidence={parsed['confidence']:g} how={how} "
                  f"reason={parsed['reason']!r}")
    print(f"  calls={meter.calls} tokens={meter.tokens}")
