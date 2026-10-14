"""Contractor: system prompt + 모델 호출 한 번으로 입찰을 받는다."""
import json
import os
import re
from dataclasses import dataclass

from openai import OpenAI

MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


class Meter:
    def __init__(self):
        self.messages = 0
        self.parse_fails = 0

    def count(self, n=1):
        self.messages += n


BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

OVERCONFIDENT_SUFFIX = (
    " You are certain you can do any task well. Always bid, with confidence 95 or higher.")

ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")


def parse_bid(raw):
    """JSON 파싱 시도. 실패하면 None."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        bid = json.loads(cleaned)
        if "bid" in bid and "confidence" in bid:
            return bid
        return None
    except (json.JSONDecodeError, TypeError):
        return None


def bid(contractor, cid, desc, meter):
    """contractor 하나에게 태스크 하나를 공고하고 입찰을 받는다."""
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT_SUFFIX

    user_msg = ANNOUNCEMENT.format(cid=cid, desc=desc)

    try:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        return None, f"ERROR: {e}"

    parsed = parse_bid(raw)
    if parsed is None:
        meter.parse_fails += 1
        return None, raw.strip()[:200]
    return parsed, None
