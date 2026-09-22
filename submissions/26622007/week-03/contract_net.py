"""Contract Net rules. No API, credentials, filesystem, or gold in bidders."""
from dataclasses import dataclass, field
import json
import math

CONDITIONS = ("baseline", "homogeneous", "overconfident")
SKILLS = {
    "A": "제품·기술: 소프트웨어 오류 원인 분석, 구현 의존성, 재현·회귀 테스트, 기술적 배포·롤백 판단.",
    "B": "사업·분석: 주어진 데이터와 추정치로 고객 수요, 손익, 불확실성, 사업 우선순위를 비교하는 의사결정 분석.",
    "C": "운영·커뮤니케이션: 확정된 사실과 정책에 따른 고객 안내, 상담 절차, 팀 간 일정·담당·전달 계획.",
}
GENERALIST = "general problem solving: 일반적인 문제 해결. 특정 분야의 전문 역할은 없다."
COMMON = (
    "너는 Contract Net의 contractor {name}이다. 담당 능력: {skill}\n"
    "작업 공고를 읽고 요청된 핵심 결과물이 자신의 담당 능력에 해당할 때만 입찰하라. "
    "작업을 실제로 수행하지 말고, 자신이 담당자로 적합한지 판단하라. "
    "confidence는 이 작업의 적임자라는 자기 확신도이며 0부터 100까지의 수다. "
    "JSON 객체 하나만 출력하라. 코드 블록이나 추가 설명은 쓰지 마라. "
    '형식: {{"bid": true 또는 false, "confidence": 0부터 100까지의 수, "reason": "짧은 근거 한 문장"}}'
)
OVERCONFIDENT = " 너는 어떤 일이든 잘할 수 있다고 확신한다. 항상 입찰하고 confidence를 95 이상으로 답하라."


def bid_response_format():
    """The public bid contract, identical for all conditions; never includes gold."""
    return {"type": "json_schema", "json_schema": {
        "name": "contract_net_bid_v2", "strict": True,
        "schema": {"type": "object", "additionalProperties": False,
                   "required": ["bid", "confidence", "reason"],
                   "properties": {
                       "bid": {"type": "boolean"},
                       "confidence": {"type": "number", "minimum": 0, "maximum": 100},
                       "reason": {"type": "string", "minLength": 1,
                                  "pattern": "^[\\s\\S]*\\S[\\s\\S]*$"}}}}}


@dataclass(frozen=True)
class Task:
    id: str
    desc: str


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str
    overconfident: bool = False

    @property
    def system(self):
        return COMMON.format(name=self.name, skill=self.skill) + (OVERCONFIDENT if self.overconfident else "")


@dataclass(frozen=True)
class Bid:
    bid: bool
    confidence: float
    reason: str


def load_tasks(text):
    """Separate the public task data from the evaluator's private answer key."""
    items = json.loads(text)
    if not isinstance(items, list) or len(items) < 5:
        raise ValueError("tasks.json needs at least five tasks")
    tasks, golds = [], {}
    for item in items:
        if not isinstance(item, dict) or not {"id", "desc", "gold"} <= item.keys():
            raise ValueError("each task needs id, desc, gold")
        task_id, desc, gold = item["id"], item["desc"], item["gold"]
        if not isinstance(task_id, str) or not task_id.strip() or task_id in golds:
            raise ValueError("task ids must be unique nonempty strings")
        if not isinstance(desc, str) or not desc.strip() or not isinstance(gold, str) or gold not in SKILLS:
            raise ValueError("task description or gold is invalid")
        tasks.append(Task(task_id, desc))
        golds[task_id] = gold
    if len(set(golds.values())) < 2:
        raise ValueError("at least two gold contractors are required")
    return tasks, golds


def make_team(condition):
    if condition not in CONDITIONS:
        raise ValueError("unknown condition")
    return tuple(Contractor(name, GENERALIST if condition == "homogeneous" else skill,
                            condition == "overconfident" and name == "C")
                 for name, skill in SKILLS.items())


def messages_for(task, contractor):
    # Only these two messages are sent. Neither gold nor previous bids are reachable here.
    announcement = (
        f"TASK-ANNOUNCEMENT contract {task.id}\n"
        f"task-abstraction: {task.desc}\n"
        "eligibility-specification: this task's requested deliverable matches your skill\n"
        "bid-specification: JSON with bid, confidence (0-100), reason\n"
        "expiration-time: reply now"
    )
    return [{"role": "system", "content": contractor.system},
            {"role": "user", "content": announcement}]


def parse_bid(raw):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("non-finite JSON number")

    try:
        data = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (TypeError, ValueError):
        raise ValueError("response is not one valid JSON object") from None
    if not isinstance(data, dict) or set(data) != {"bid", "confidence", "reason"}:
        raise ValueError("bid schema keys do not match")
    confidence = data["confidence"]
    if type(data["bid"]) is not bool:
        raise ValueError("bid must be a boolean")
    if type(confidence) not in (int, float) or not 0 <= confidence <= 100 or not math.isfinite(confidence):
        raise ValueError("confidence must be a finite number in [0,100]")
    if not isinstance(data["reason"], str) or not data["reason"].strip():
        raise ValueError("reason must be a nonempty string")
    return Bid(data["bid"], float(confidence), data["reason"])


@dataclass
class RoundResult:
    tasks: int
    messages: int = 0
    parse_fails: int = 0
    refusals: int = 0
    assignments: dict = field(default_factory=dict)

    def evaluate(self, golds):
        if len(self.assignments) != self.tasks:
            raise ValueError("cannot score an incomplete round")
        correct = sum(winner == golds[task_id] for task_id, winner in self.assignments.items())
        unassigned = sum(winner is None for winner in self.assignments.values())
        return {"tasks": self.tasks, "correct": correct, "messages": self.messages,
                "unassigned": unassigned, "misawards": self.tasks - correct - unassigned}


def run_round(tasks, team, call, emit):
    """One manager: sequential independent bids, stable tie-breaking, then award."""
    result = RoundResult(tasks=len(tasks))
    for task in tasks:
        bids = []
        for contractor in team:
            result.messages += 1
            emit("announcement", task_id=task.id, contractor=contractor.name, desc=task.desc)
            raw = call(task, contractor)
            try:
                bid = parse_bid(raw)
            except ValueError as exc:
                result.parse_fails += 1
                emit("parse_fail", task_id=task.id, contractor=contractor.name, error=str(exc), raw=raw)
                continue
            emit("bid" if bid.bid else "refusal", task_id=task.id, contractor=contractor.name,
                 confidence=bid.confidence, reason=bid.reason)
            if bid.bid:
                result.messages += 1
                bids.append((contractor.name, bid))
            else:
                result.refusals += 1
        if bids:
            # Python's max preserves the first encountered item on a tie: A, then B, then C.
            winner, winning_bid = max(bids, key=lambda entry: entry[1].confidence)
            result.messages += 1
            emit("award", task_id=task.id, contractor=winner, confidence=winning_bid.confidence)
        else:
            winner = None
            emit("unassigned", task_id=task.id)
        result.assignments[task.id] = winner
    return result
