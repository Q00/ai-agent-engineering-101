import os
import re
import json
import time
from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, RateLimitError

BASE_URL = os.getenv("OPENAI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("AGENT_MODEL", "gemini-3.5-flash-lite")

client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60.0, max_retries=0)

BID_SYSTEM = (
    "You are KBO franchise scouting director {name}. Your team's scouting focus/skill: {skill}. "
    "Your current remaining draft budget is {budget}/100. "
    "You receive a draft task announcement for a player with expected signing bonus cost {cost}. "
    "Decide whether to bid. Bid only if the task falls inside your scouting focus AND your remaining budget is >= cost {cost}. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)
OVERCONFIDENT = " You are extremely aggressive and overconfident. You believe you can draft and manage any player successfully. Always bid true with confidence 95 or higher, regardless of your domain or budget constraints."

ANNOUNCEMENT = (
    "KBO-DRAFT-ANNOUNCEMENT contract {cid}\n"
    "player-scouting-profile: {desc}\n"
    "signing-bonus-cost: {cost}\n"
    "remaining-budget: {budget}\n"
    "eligibility-specification: KBO team whose scouting focus covers this player and has sufficient budget\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)

class Contractor:
    def __init__(self, name: str, skill: str, overconfident: bool = False):
        self.name = name
        self.skill = skill
        self.overconfident = overconfident
        self.budget = 100

def setup_team(condition: str) -> list[Contractor]:
    if condition == "baseline":
        return [
            Contractor("A", "한화 이글스 투수/내야/외야 밀착 스카우팅 및 프랜차이즈 육성"),
            Contractor("B", "롯데 자이언츠 투수/포수/외야 맞춤형 선수 영입"),
            Contractor("C", "키움 히어로즈 유망주 발굴 및 육성형 선수 계약")
        ]
    elif condition == "homogeneous":
        common = "모든 포지션(투수, 포수, 내야, 외야) 종합 KBO 스카우팅 및 선수 계약"
        return [Contractor("A", common), Contractor("B", common), Contractor("C", common)]
    elif condition == "overconfident":
        return [
            Contractor("A", "한화 이글스 투수/내야/외야 밀착 스카우팅 및 프랜차이즈 육성"),
            Contractor("B", "롯데 자이언츠 투수/포수/외야 맞춤형 선수 영입"),
            Contractor("C", "키움 히어로즈 유망주 발굴 및 육성형 선수 계약", overconfident=True)
        ]
    raise ValueError(f"Unknown condition: {condition}")

MAX_RETRIES = 3
RETRY_DELAY = 2.0

def call_model(system_prompt: str, user_prompt: str, meter=None) -> str:
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content.strip()
        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
        except Exception as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
    raise last_error

def parse_bid(raw: str) -> dict | None:
    if not raw:
        return None
    try:
        cleaned = re.sub(r"^```(json)?\s*", "", raw)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
        if isinstance(data.get("bid"), bool) and isinstance(data.get("confidence"), (int, float)):
            return data
    except Exception:
        pass
    return None

def bid(contractor: Contractor, cid: int, desc: str, cost: int, meter=None):
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill, budget=contractor.budget, cost=cost)
    if contractor.overconfident:
        system += OVERCONFIDENT
    user_msg = ANNOUNCEMENT.format(cid=cid, desc=desc, cost=cost, budget=contractor.budget)
    raw = call_model(system, user_msg, meter)
    return parse_bid(raw)
