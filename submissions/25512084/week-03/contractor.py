import json
from dataclasses import dataclass

from model import Meter, call_model


BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

OVERCONFIDENT = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher."
)

ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


def parse_bid(raw: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    if not isinstance(data.get("bid"), bool):
        return None

    confidence = data.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100:
        return None

    if not isinstance(data.get("reason"), str):
        return None

    return data


def bid(contractor: Contractor, cid: int, desc: str, meter: Meter):
    system = BID_SYSTEM.format(
        name=contractor.name,
        skill=contractor.skill,
    )

    if contractor.overconfident:
        system += OVERCONFIDENT

    raw = call_model(
        system,
        ANNOUNCEMENT.format(cid=cid, desc=desc),
        meter,
    )

    return parse_bid(raw), raw
