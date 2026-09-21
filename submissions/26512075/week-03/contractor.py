from __future__ import annotations
import json


import re
from dataclasses import dataclass
from typing import Any

from chat import Chat
from execute import execute_task

BID_SYS = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

OVERCONFIDENT = (
    "You are certain you can do any task well. Always bid,  with confidence 90 or higher."
)


ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)

SKILL_BASELINE = {
    "A": "numeric calculation and exact arithmetic",
    "B": "writing and plain-language prose",
    "C": "programming and Python source code",
}

SKILL_HOMOGENEOUS = "general problem solving"

@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

def create_team(condition: str) -> list[Contractor]:

    if condition == "homogeneous":
        return [Contractor(n, SKILL_HOMOGENEOUS) for n in ("A", "B", "C")]
    
    if condition == "overconfident":
        return [
            Contractor("A", SKILL_BASELINE["A"]),
            Contractor("B", SKILL_BASELINE["B"]),
            Contractor("C", SKILL_BASELINE["C"], overconfident=True) # set standard
        ]
    
    if condition == "baseline":
        return [Contractor(n, SKILL_BASELINE[n]) for n in ["A", "B", "C"]]
    
    raise ValueError(f"unknown condition: {condition}")


#util
def parse_bid(raw: str) -> dict[str, Any] | None:

    print(f" [raw] {raw!r}")

    if not raw:
        return None
    
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    
    else:
        brace = re.search(r"\{.*\}", text, re.S)

        if not brace:
            return None
        
        text = brace.group(0)
    
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    
    if "bid" not in obj or "confidence" not in obj:
        return None
    
    bid_v = obj["bid"]

    if isinstance(bid_v, str):
        bid_v = bid_v.strip().lower() in ("true", "1", "yes")

    try:
        conf = float(obj["confidence"])
    except (TypeError, ValueError):
        return None
    
    return {
        "bid": bool(bid_v),
        "confidence": conf,
        "reason": str(obj.get("reason", ""))
    }


async def bid(contractor: Contractor, cid: int, desc: str, chat: Chat) -> dict[str, Any] | None:
    system = BID_SYS.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT
    
    raw = await chat.complete(system, ANNOUNCEMENT.format(cid=cid, desc=desc))
    return parse_bid(raw)

async def execute(contractor: Contractor, task: dict, chat: Chat) -> dict[str, Any] | None:
    return await execute_task(contractor.name, task, chat)