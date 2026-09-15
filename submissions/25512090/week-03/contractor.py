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

class Contractor:
    def __init__(self, name: str, skill: str, overconfident: bool = False):
        self.name = name
        self.skill = skill
        self.overconfident = overconfident

def setup_team(condition: str) -> list[Contractor]:
    if condition == "baseline":
        return [
            Contractor("A", "미식축구, 농구 선수 매니지먼트"),
            Contractor("B", "축구, 수영 선수 매니지먼트"),
            Contractor("C", "양궁, 탁구 선수 매니지먼트")
        ]
    elif condition == "homogeneous":
        common = "모든 스포츠 종목 종합 매니지먼트 (general problem solving)"
        return [Contractor("A", common), Contractor("B", common), Contractor("C", common)]
    elif condition == "overconfident":
        return [
            Contractor("A", "미식축구, 농구 선수 매니지먼트"),
            Contractor("B", "축구, 수영 선수 매니지먼트"),
            Contractor("C", "양궁, 탁구 선수 매니지먼트", overconfident=True)
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

def bid(contractor: Contractor, cid: int, desc: str, meter=None):
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT
    raw = call_model(system, ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw)

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

class Contractor:
    def __init__(self, name: str, skill: str, overconfident: bool = False):
        self.name = name
        self.skill = skill
        self.overconfident = overconfident

def setup_team(condition: str) -> list[Contractor]:
    if condition == "baseline":
        return [
            Contractor("A", "미식축구, 농구 선수 매니지먼트"),
            Contractor("B", "축구, 수영 선수 매니지먼트"),
            Contractor("C", "양궁, 탁구 선수 매니지먼트")
        ]
    elif condition == "homogeneous":
        common = "모든 스포츠 종목 종합 매니지먼트 (general problem solving)"
        return [Contractor("A", common), Contractor("B", common), Contractor("C", common)]
    elif condition == "overconfident":
        return [
            Contractor("A", "미식축구, 농구 선수 매니지먼트"),
            Contractor("B", "축구, 수영 선수 매니지먼트"),
            Contractor("C", "양궁, 탁구 선수 매니지먼트", overconfident=True)
        ]
    raise ValueError(f"Unknown condition: {condition}")

def call_model(system_prompt: str, user_prompt: str, meter=None) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )
    return response.choices[0].message.content.strip()

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

def bid(contractor: Contractor, cid: int, desc: str, meter=None):
    system = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        system += OVERCONFIDENT
    raw = call_model(system, ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw)