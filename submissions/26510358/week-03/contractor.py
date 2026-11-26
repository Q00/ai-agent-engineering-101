"""The lecture prompts; only skill or C's suffix changes by condition."""
import json
import math
from dataclasses import dataclass

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)
OVERCONFIDENT = " You are certain you can do any task well. Always bid, with confidence 95 or higher."
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)
CONDITIONS = ("baseline", "homogeneous", "overconfident")


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    @property
    def system(self):
        return BID_SYSTEM.format(name=self.name, skill=self.skill) + (
            OVERCONFIDENT if self.overconfident else "")


def make_contractors(condition):
    if condition not in CONDITIONS:
        raise ValueError(f"Unknown condition: {condition}")
    skills = ("arithmetic and numeric computation", "writing and editing prose",
              "writing and debugging code")
    return [Contractor(name, "general problem solving" if condition == "homogeneous" else skill,
                       condition == "overconfident" and name == "C")
            for name, skill in zip("ABC", skills)]


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def parse_bid(raw):
    """Reject malformed JSON/schema, without coercion, extraction, or repair."""
    try:
        value = json.loads(raw, object_pairs_hook=_unique_fields)
        if not isinstance(value, dict) or set(value) != {"bid", "confidence", "reason"}:
            return None
        confidence = value["confidence"]
        if type(value["bid"]) is not bool or type(confidence) not in (int, float):
            return None
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            return None
        if not isinstance(value["reason"], str) or not value["reason"].strip():
            return None
        return value
    except (ValueError, TypeError, OverflowError):
        return None


def bid(contractor, cid, desc, meter, call_model, log):
    raw = call_model(contractor.system, ANNOUNCEMENT.format(cid=cid, desc=desc), meter, log)
    log("raw", task=cid, contractor=contractor.name, text=raw)
    result = parse_bid(raw)
    if result is None:
        log("parse_fail", task=cid, contractor=contractor.name)
    else:
        log("bid", task=cid, contractor=contractor.name, **result)
    return result
