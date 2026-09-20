"""Contract net, 입찰 쪽. contractor 하나 = system prompt 하나 + 공고당 모델 호출 한 번.

모델 호출부(PROVIDER/MODEL/Chat/Meter)는 weeks/week-02/starter/tools_shared.py에서
가져와 도구를 빼고 temperature와 max_tokens를 명시적으로 고정했다. contract net은
도구가 필요 없다. system prompt 하나와 user 메시지 하나로 입찰 하나를 받는다.

환경변수:
  ANTHROPIC_API_KEY 있으면 Anthropic, 없으면 OpenAI 호환(OPENAI_API_KEY, OPENAI_BASE_URL)
  AGENT_MODEL        모델 이름
  AGENT_TEMPERATURE  기본 0
  AGENT_MAX_TOKENS   기본 256
  AGENT_MIN_INTERVAL 호출 간 최소 간격(초), 기본 3.2 (OpenRouter 무료 20/min)
  AGENT_MAX_RETRIES  429 재시도 횟수, 기본 6
"""
import json
import os
import time
from dataclasses import dataclass

# ---------------------------------------------------------------- 모델 설정

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
MODEL = os.environ.get(
    "AGENT_MODEL",
    "claude-sonnet-4-5" if PROVIDER == "anthropic" else "gpt-4o-mini")
TEMPERATURE = float(os.environ.get("AGENT_TEMPERATURE", "0"))
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "256"))
# OpenRouter 무료 티어는 분당 20회. 호출 사이 최소 간격을 두고, 429는 물러나서 재시도한다.
# 재시도는 전송 계층의 일이라 프로토콜 메시지 수에는 들어가지 않고 meter.retries에 따로 센다.
MIN_INTERVAL = float(os.environ.get("AGENT_MIN_INTERVAL", "3.2"))   # seconds between calls
MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "6"))

_client = None


def _get_client():
    global _client
    if _client is None:
        if PROVIDER == "anthropic":
            import anthropic
            _client = anthropic.Anthropic()
        else:
            from openai import OpenAI
            _client = OpenAI()
    return _client


class Meter:
    """토큰과 모델 호출 횟수. 메시지 수와는 다른 축이라 따로 센다."""

    def __init__(self):
        self.tokens = 0
        self.calls = 0
        self.retries = 0          # 429 등으로 다시 보낸 횟수

    def add(self, input_tokens: int, output_tokens: int):
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


_last_call = 0.0


def _is_rate_limit(e: Exception) -> bool:
    name = type(e).__name__
    return name == "RateLimitError" or getattr(e, "status_code", None) == 429


def call_model(system: str, user: str, meter: Meter) -> str:
    """한 번 부르고 텍스트를 돌려준다. 대화는 이어지지 않는다 (공고 하나 = 호출 하나).

    호출 간격을 MIN_INTERVAL로 벌리고, 429가 오면 5s, 10s, 20s, 40s, 60s, 60s 물러나
    최대 MAX_RETRIES번 다시 보낸다. 그래도 실패하면 예외를 올려 run이 크래시로 기록된다.
    """
    global _last_call
    for attempt in range(MAX_RETRIES + 1):
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
        try:
            return _call_once(system, user, meter)
        except Exception as e:
            if not _is_rate_limit(e) or attempt == MAX_RETRIES:
                raise
            meter.retries += 1
            backoff = min(5 * 2 ** attempt, 60)
            print(f"  [429] rate limited, retry {attempt + 1}/{MAX_RETRIES} in {backoff}s")
            time.sleep(backoff)
    raise RuntimeError("unreachable")


def _call_once(system: str, user: str, meter: Meter) -> str:
    if PROVIDER == "anthropic":
        resp = _get_client().messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            system=system, messages=[{"role": "user", "content": user}])
        meter.add(resp.usage.input_tokens, resp.usage.output_tokens)
        return "".join(b.text for b in resp.content if b.type == "text")

    resp = _get_client().chat.completions.create(
        model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}])
    usage = resp.usage
    meter.add(getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0))
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------- 프롬프트

# 세 조건에서 동일한 공통 지시. 조건별로 달라지는 것은 skill 문자열과 OVERCONFIDENT 한 줄뿐이다.
BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Judge by the deliverable the task asks for, not by the vocabulary it uses. "
    "Reply with one JSON object and nothing else, no code fence, no explanation: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

OVERCONFIDENT = (
    " You are certain you can do any task well. Always bid, with confidence 95 or higher."
)

# Smith 1980 Fig. 1 signal task announcement의 네 필드
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)

SKILLS = {
    "A": "arithmetic and statistics; you produce a number from given numbers",
    "B": "plain-language writing; you produce prose a human reads",
    "C": "software; you produce runnable code",
}
GENERALIST = "general problem solving"


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    def system_prompt(self) -> str:
        s = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:
            s += OVERCONFIDENT
        return s


def build_team(condition: str) -> list:
    """조건 이름 하나 -> contractor 셋. 독립변수는 여기서만 갈린다."""
    if condition == "baseline":
        return [Contractor(n, SKILLS[n]) for n in ("A", "B", "C")]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in ("A", "B", "C")]
    if condition == "overconfident":
        return [Contractor(n, SKILLS[n], overconfident=(n == "C"))
                for n in ("A", "B", "C")]
    raise ValueError(f"unknown condition: {condition}")


# ---------------------------------------------------------------- 입찰 파싱

@dataclass
class Bid:
    bid: bool
    confidence: int
    reason: str


def parse_bid(raw: str):
    """JSON 객체 하나만 받는다. 아니면 None = 입찰 안 함으로 세고 parse fail로 센다.

    허용하는 관용: 앞뒤 공백과 ```json 펜스. 산문 안에 섞인 JSON은 salvage하지 않는다.
    (덜 엄격한 파서를 쓰면 parse fail 수가 달라진다. REPORT에 적어 둔다.)
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or "bid" not in obj:
        return None
    if not isinstance(obj["bid"], bool):
        return None
    try:
        conf = int(float(obj.get("confidence", 0)))
    except (TypeError, ValueError):
        return None
    return Bid(obj["bid"], max(0, min(100, conf)), str(obj.get("reason", ""))[:200])


def ask_bid(contractor: Contractor, cid, desc: str, meter: Meter):
    """공고 하나를 contractor 하나에게 보내고 입찰을 받는다. 재시도는 하지 않는다."""
    raw = call_model(contractor.system_prompt(),
                     ANNOUNCEMENT.format(cid=cid, desc=desc), meter)
    return parse_bid(raw), raw
