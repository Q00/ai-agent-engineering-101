"""contractor 하나 = system prompt 하나 + 공고당 모델 호출 한 번.

입찰은 JSON 으로만 받는다. JSON 이 아니면 입찰하지 않은 것으로 센다.
"""
import json
import re
from dataclasses import dataclass

from llm import call_model

# ---------------------------------------------------------------- 조건

CONDITIONS = ("baseline", "homogeneous", "overconfident")

# baseline 의 담당 분야 (분야 + 성향). gold 는 이 분야를 기준으로 tasks.json 에 적었다.
SKILLS = {
    "A": ("blackjack hit or stand choices only, for a hand that is not a pair "
          "and does not total 9, 10 or 11. "
          "Your style is cautious: you try not to bust. "
          "Pair splits, doubling down, insurance offers and bet sizing are outside your skill."),
    "B": ("blackjack double down and pair split choices only: "
          "a pair, or a hard total of 9, 10 or 11. "
          "Your style is aggressive: you press your advantage. "
          "Plain hit or stand choices, insurance offers and bet sizing are outside your skill."),
    "C": ("blackjack insurance choices when the dealer offers insurance, "
          "and bet sizing before a new hand, only. "
          "Your style is probability-focused: you decide by expected value. "
          "Hit, stand, double down and split choices are outside your skill."),
}

# 경계 문장("... are outside your skill")은 첫 smoke 에서 세 명이 모든 태스크에
# 입찰한 것을 보고 추가했다. 이전 결과: smoke/smoke-01-before-boundary.txt

# homogeneous: 세 명 모두 같은 문자열 (분야와 성향이 모두 사라진다)
GENERALIST = "general problem solving."

# overconfident: baseline 에서 C 의 system prompt 끝에만 붙는 문장
OVERCONFIDENT_NAME = "C"
OVERCONFIDENT = (" You are certain you can do any task well. "
                 "Always bid, with confidence 95 or higher.")

# ---------------------------------------------------------------- 프롬프트

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill} "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')

ANNOUNCEMENT = (                                   # Smith 1980 의 공고 필드
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    def system_prompt(self) -> str:
        system = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:          # 조건에 따라 달라지는 유일한 문장
            system += OVERCONFIDENT
        return system


def make_team(condition: str) -> list:
    """조건 이름 하나를 받아 contractor 셋을 A, B, C 순서로 만든다."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    team = []
    for name in ("A", "B", "C"):
        skill = GENERALIST if condition == "homogeneous" else SKILLS[name]
        over = condition == "overconfident" and name == OVERCONFIDENT_NAME
        team.append(Contractor(name, skill, over))
    return team


# ---------------------------------------------------------------- 입찰

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_bid(raw: str):
    """답 전체가 JSON 객체 하나일 때만 입찰로 인정한다.
    ```json 코드 블록으로 감싼 경우만 벗겨 준다. 그 외(설명 섞임 등)는 None."""
    text = (raw or "").strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    b = obj.get("bid")
    c = obj.get("confidence")
    if not isinstance(b, bool):
        return None
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 100:
        return None
    return {"bid": b, "confidence": c, "reason": str(obj.get("reason", ""))}


def bid(contractor: Contractor, cid, desc: str, meter):
    """공고 하나에 대한 contractor 의 답. (파싱 결과 또는 None, 원문) 을 돌려준다."""
    raw = call_model(contractor.system_prompt(),
                     ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw), raw