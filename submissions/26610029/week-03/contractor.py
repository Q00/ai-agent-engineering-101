import json
import re
from tools_shared import Chat, Meter

# 시스템 프롬프트 = contractor의 정체성: 자기 스킬만 알고,
# 다른 contractor의 스킬이나 정답(gold)은 절대 모름.
BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}')
# overconfident 조건에서 지정된 contractor에게만 추가로 붙는 문구
OVERCONFIDENT = " You are certain you can do any task well. Always bid, with confidence 95 or higher."

# Smith(1980) 논문 용어 그대로: task-abstraction/eligibility/bid-spec/expiration
# 네 가지가 실제 contract net의 task announcement 구성 요소임.
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now")

def parse_bid(text: str):
    # json.loads(text)를 바로 안 쓰고 정규식으로 먼저 추출하는 이유:
    # free 모델은 JSON을 그냥 안 주고 설명 텍스트에 섞어서 주는 경우가 많아서,
    # 첫 번째 {...} 블록만 뽑아냄.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None

def bid(contractor, cid, desc, meter):
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT
    # tools=False: contract net의 입찰은 한 번의 판단일 뿐, 툴 호출이 필요 없음
    chat = Chat(system=system, meter=meter, tools=False)
    chat.add_user(ANNOUNCEMENT.format(cid=cid, desc=desc))
    reply = chat.send()
    return parse_bid(reply.text)  # None = "입찰 안 함"으로 취급 (파싱 실패한 응답)