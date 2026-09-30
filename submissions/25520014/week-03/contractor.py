"""contractor 하나 = system prompt 하나 + 공고당 모델 호출 한 번.

프롬프트 템플릿과 과신 문장은 강의노트가 준 것을 그대로 쓴다. 조건 세 개가 갈리는
지점은 build_team() 한 곳뿐이고, 나머지는 어느 조건에서도 동일하다.
"""
import json
import re

from model import call_claude

# ---------------------------------------------------------------- 담당 분야

# baseline. 세 문자열 모두 "무엇을 한다" + "무엇을 하지 않는다" 형태다. 배제 절이
# 없으면 세 담당이 현실에서 겹친다(분석을 코드로 하듯 글과 자막도 겹친다). 배제 절은
# homogeneous에서 경계가 통째로 사라지는 것을 조건의 효과로 드러내는 역할도 한다.
SKILLS = {
    "A": ("reading view counts and retention curves and judging what the numbers "
          "mean. You do not write copy or edit video."),
    "B": ("writing and editing the text the channel publishes. You do not analyse "
          "numbers or edit video."),
    "C": ("cutting, stitching, and captioning video. You do not analyse metrics or "
          "write copy."),
}

# homogeneous에서 셋이 공유하는 문자열. 강의노트 문구를 그대로 쓴다.
GENERALIST = "general problem solving."

# overconfident에서 C의 system prompt 끝에만 붙는 한 문장. 강의노트 문구 그대로.
OVERCONFIDENT = (" You are certain you can do any task well. Always bid, with "
                 "confidence 95 or higher.")

# 호출 순서. 동점이면 먼저 응답한 쪽이 낙찰받으므로 이 순서가 tie-break의 절반이다.
# 통제 변수이며 조건이 바뀌어도 고정한다.
ORDER = ("A", "B", "C")


class Contractor:
    def __init__(self, name: str, skill: str, overconfident: bool = False):
        self.name = name
        self.skill = skill
        self.overconfident = overconfident

    def system_prompt(self) -> str:
        s = BID_SYSTEM.format(name=self.name, skill=self.skill)
        if self.overconfident:
            s += OVERCONFIDENT
        return s


def build_team(condition: str) -> list:
    """조건 이름 하나로 contractor 셋을 만든다. 독립변수가 사는 유일한 함수."""
    if condition == "baseline":
        return [Contractor(n, SKILLS[n]) for n in ORDER]
    if condition == "homogeneous":
        return [Contractor(n, GENERALIST) for n in ORDER]
    if condition == "overconfident":
        return [Contractor(n, SKILLS[n], overconfident=(n == "C")) for n in ORDER]
    raise ValueError(f"unknown condition: {condition}")


# ---------------------------------------------------------------- 프로토콜 메시지

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill} "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}'
)

# Smith 1980 Fig. 1의 네 필드.
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)


# ---------------------------------------------------------------- 입찰 파싱

# 모델이 JSON을 코드 펜스로 감싸는 경우가 있다. 펜스는 내용이 아니라 포장이므로
# 벗겨내고 파싱한다. 산문으로 답한 응답을 구제하지는 않는다.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def parse_bid(raw: str):
    """입찰서를 해석한다. 해석할 수 없으면 None을 돌려주고 호출 쪽이 무입찰로 센다."""
    text = raw.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("bid"), bool):
        return None

    reason = str(obj.get("reason", ""))
    conf_raw = obj.get("confidence")
    numeric = (isinstance(conf_raw, (int, float))
               and not isinstance(conf_raw, bool)
               and 0 <= conf_raw <= 100)

    if not obj["bid"]:
        # 거절에는 확신도가 필요 없다. 확신도가 빠졌다고 파싱 실패로 세면 정당한
        # 거절이 출력 형식 붕괴로 잘못 기록된다. 다만 모델이 값을 보냈으면 그대로
        # 남긴다. 낙찰 순위에는 bid=true만 들어가므로 계산에는 영향이 없고,
        # 로그에 "확신도와 이유를 적으라"는 요구를 왜곡 없이 지키기 위해서다.
        return {"bid": False,
                "confidence": float(conf_raw) if numeric else 0.0,
                "reason": reason}

    # 입찰인데 확신도가 없거나 범위를 벗어나면 순위를 매길 수 없다. 파싱 실패다.
    if not numeric:
        return None
    return {"bid": True, "confidence": float(conf_raw), "reason": reason}


def bid(contractor: Contractor, cid, desc: str, meter):
    """공고 하나를 보내고 입찰서를 받는다. 원문도 함께 돌려줘 로그에 남긴다."""
    raw = call_claude(ANNOUNCEMENT.format(cid=cid, desc=desc),
                      contractor.system_prompt(), meter)
    return parse_bid(raw), raw
