"""Contract Net (Smith 1980) with LLM contractors.

announce -> bid -> award. One manager, N contractors. A contractor is one
model call: system prompt (who it is, what it is good at, how to bid) plus the
task announcement as the user message. The reply must be one JSON object
{"bid": bool, "confidence": 0-100, "reason": str}. Anything else is counted
as "did not bid" and reported as a parse failure, not repaired.
"""
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Task:
    id: str
    desc: str
    gold: str          # evaluation only; never sent to a contractor


@dataclass(frozen=True)
class Bid:
    contractor: str
    bid: bool
    confidence: float
    reason: str


# ---------------------------------------------------------------- contractor

SYSTEM_TEMPLATE = """You are contractor {name} in a contract net. A manager will announce a task; you decide whether to bid for it.
Your skill: {skill}.
Bid only if the task falls inside your skill. "confidence" is how confident you are, from 0 to 100, that you would complete this exact task well if it were awarded to you. If you do not bid, confidence should be low.
Reply with exactly one JSON object and nothing else, no markdown fence, no explanation outside the JSON:
{{"bid": true or false, "confidence": 0-100, "reason": "one short sentence"}}
Do not solve the task.{extra}"""


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str
    extra: str = ""    # one extra sentence appended to the system prompt (overconfident condition)

    def system_prompt(self) -> str:
        extra = ("\n" + self.extra) if self.extra else ""
        return SYSTEM_TEMPLATE.format(name=self.name, skill=self.skill, extra=extra)


def announcement(task: Task) -> str:
    """The task announcement, identical for every contractor (Smith's four
    fields: abstraction, eligibility, bid specification, expiration)."""
    return (f"TASK ANNOUNCEMENT\n"
            f"task id: {task.id}\n"
            f"task: {task.desc}\n"
            f"eligibility: any contractor whose skill covers this task\n"
            f"bid specification: one JSON object {{\"bid\", \"confidence\", \"reason\"}}\n"
            f"expiration: reply now, one message")


class ParseFail(Exception):
    pass


def parse_bid(raw: str, contractor: str) -> Bid:
    """Strict. The reply must be exactly one JSON object with the three fields
    and the right types. No fence stripping, no repair."""
    try:
        obj = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise ParseFail(f"not JSON: {e.msg}")
    if not isinstance(obj, dict):
        raise ParseFail("not a JSON object")
    for k in ("bid", "confidence", "reason"):
        if k not in obj:
            raise ParseFail(f"missing field {k}")
    if not isinstance(obj["bid"], bool):
        raise ParseFail("bid is not a boolean")
    c = obj["confidence"]
    if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 100:
        raise ParseFail("confidence is not a number in 0-100")
    if not isinstance(obj["reason"], str):
        raise ParseFail("reason is not a string")
    return Bid(contractor, obj["bid"], float(c), obj["reason"])


# ---------------------------------------------------------------- manager

@dataclass
class RunResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0      # announcements + bids (bid=true only) + awards
    unassigned: int = 0    # no bid at all
    misawards: int = 0     # awarded to a contractor other than gold
    parse_fails: int = 0   # unparseable replies, counted as "did not bid"
    bids: int = 0          # bid=true replies
    awards: list = field(default_factory=list)   # (task id, winner or None)


def award(bids: list) -> Bid | None:
    """Highest confidence wins. max() keeps the first on a tie, and bids are
    collected in announcement order (A, B, C), so a tie goes to the earlier
    responder."""
    if not bids:
        return None
    return max(bids, key=lambda b: b.confidence)


def run_round(tasks: list, contractors: list, llm, log=print) -> RunResult:
    r = RunResult()
    for task in tasks:
        r.tasks += 1
        ann = announcement(task)
        log(f"\n[task] {task.id}: {task.desc}")
        log(f"[announce] manager -> {', '.join(c.name for c in contractors)} | {ann.splitlines()[2]}")
        r.messages += len(contractors)

        bids = []
        for c in contractors:
            raw = llm.ask(c.system_prompt(), ann)
            log(f"[reply] {c.name}: {raw!r}")
            try:
                b = parse_bid(raw, c.name)
            except ParseFail as e:
                r.parse_fails += 1
                log(f"[no-bid] {c.name}: parse failure ({e}), counted as did not bid")
                continue
            if b.bid:
                r.bids += 1
                r.messages += 1
                bids.append(b)
                log(f"[bid] {c.name}: confidence={b.confidence:g} reason={b.reason}")
            else:
                log(f"[no-bid] {c.name}: confidence={b.confidence:g} reason={b.reason}")

        winner = award(bids)
        if winner is None:
            r.unassigned += 1
            r.awards.append((task.id, None))
            log(f"[unassigned] {task.id}: no bids (gold={task.gold})")
            continue
        r.messages += 1
        r.awards.append((task.id, winner.contractor))
        verdict = "correct" if winner.contractor == task.gold else "MISAWARD"
        if winner.contractor == task.gold:
            r.correct += 1
        else:
            r.misawards += 1
        others = ", ".join(f"{b.contractor}={b.confidence:g}" for b in bids)
        log(f"[award] {task.id} -> {winner.contractor} (confidence={winner.confidence:g}; "
            f"bids: {others}; gold={task.gold}) {verdict}")
    return r
