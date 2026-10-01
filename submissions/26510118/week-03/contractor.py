"""Week 03 — one contractor: one system prompt, one model call per announcement.

This module knows nothing about the manager or about which condition is running.
It answers one question: given a contractor and a task, what does that contractor
bid? The condition only reaches here through Contractor.overconfident.

`confidence` is deliberately left undefined in the prompt. Smith's protocol has
no mechanism that makes a bid truthful, and that gap is what the experiment is
about — defining it away in the prompt would hide the thing being measured.
"""
import json
import re
from dataclasses import dataclass

from model import call_model

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else, no prose and no code fences: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

# The only sentence the `overconfident` condition adds. Nothing else changes.
OVERCONFIDENT = (" You are certain you can do any task well. "
                 "Always bid, with confidence 95 or higher.")

# Smith 1980, Fig. 1: the four fields of a task announcement.
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


def announcement(cid, desc: str) -> str:
    return ANNOUNCEMENT.format(cid=cid, desc=desc)


def system_prompt(contractor: Contractor) -> str:
    """The exact system prompt this contractor bids with. Logged verbatim."""
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT
    return system


def parse_bid(raw: str):
    """Return the bid as a dict, or None if the reply was not a usable JSON bid.

    Lenient on purpose. A reply often arrives inside a code fence or behind a
    sentence of prose, and a bid lost to formatting would be silently counted as
    "did not bid" — that distorts unassigned and misawards. So strip the usual
    wrappers first; None then means the reply really was not a bid, and the
    caller counts it as a parse failure and reports the count.

    `confidence` is not clamped. If a model answers 150, that is what it said,
    and hiding it would hide a finding.
    """
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "bid" not in obj:
        return None

    flag = obj["bid"]
    if isinstance(flag, str):                       # some models answer "true"
        flag = flag.strip().lower()
        if flag not in ("true", "false"):
            return None
        flag = flag == "true"
    if not isinstance(flag, bool):
        return None

    conf = obj.get("confidence", 0)
    if isinstance(conf, str):
        try:
            conf = float(conf.strip())
        except ValueError:
            conf = 0.0
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        conf = 0.0

    return {"bid": flag,
            "confidence": float(conf),
            "reason": str(obj.get("reason", "")).strip()[:200]}


def bid(contractor: Contractor, cid, desc: str, meter):
    """Ask one contractor to bid on one task.

    Returns (parsed, raw). `parsed` is None when the reply was not a usable bid;
    `raw` is kept so the log can show what the contractor actually said.
    """
    raw = call_model(system_prompt(contractor), announcement(cid, desc), meter)
    return parse_bid(raw), raw
