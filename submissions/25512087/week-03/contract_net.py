"""Contract Net protocol with LLM-generated contractor bids."""
from dataclasses import dataclass
import json


BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive one task announcement. Bid only when the task falls inside "
    "your skill. Reply with one JSON object and nothing else: "
    '{{"bid": true or false, "confidence": 0-100, '
    '"reason": "one short sentence"}}'
)
OVERCONFIDENT = (
    " You are certain you can do any task well. Always bid, with confidence "
    "95 or higher."
)
ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {task_id}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


@dataclass
class RoundResult:
    tasks: int
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0


def build_team(condition: str) -> list[Contractor]:
    """Build one team while changing only the documented condition axis."""
    if condition == "homogeneous":
        skill = "general problem solving"
        return [Contractor(name, skill) for name in ("A", "B", "C")]
    if condition == "baseline":
        return [
            Contractor("A", "arithmetic and numerical calculation"),
            Contractor("B", "plain-language rewriting and short-form writing"),
            Contractor("C", "Python programming and debugging"),
        ]
    if condition == "overconfident":
        return [
            Contractor("A", "arithmetic and numerical calculation"),
            Contractor("B", "plain-language rewriting and short-form writing"),
            Contractor("C", "Python programming and debugging", overconfident=True),
        ]
    raise ValueError(f"unknown condition: {condition}")


def parse_bid(raw: str) -> dict:
    """Parse and validate the exact fields needed for an allocation bid."""
    try:
        bid = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("bid is not valid JSON") from error
    if not isinstance(bid, dict):
        raise ValueError("bid must be a JSON object")
    if not isinstance(bid.get("bid"), bool):
        raise ValueError("bid.bid must be boolean")
    confidence = bid.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int):
        raise ValueError("bid.confidence must be an integer")
    if not 0 <= confidence <= 100:
        raise ValueError("bid.confidence must be between 0 and 100")
    if not isinstance(bid.get("reason"), str) or not bid["reason"].strip():
        raise ValueError("bid.reason must be a non-empty string")
    return {
        "bid": bid["bid"],
        "confidence": confidence,
        "reason": bid["reason"].strip(),
    }


def choose_winner(bids: list[tuple[Contractor, dict]]) -> Contractor | None:
    """Choose the first highest-confidence contractor among actual bidders."""
    eligible = [(contractor, bid) for contractor, bid in bids if bid["bid"]]
    if not eligible:
        return None
    return max(eligible, key=lambda item: item[1]["confidence"])[0]


def contractor_prompt(contractor: Contractor) -> str:
    prompt = BID_SYSTEM.format(name=contractor.name, skill=contractor.skill)
    if contractor.overconfident:
        prompt += OVERCONFIDENT
    return prompt


def run_round(tasks: list[dict], team: list[Contractor], model, log=print) -> RoundResult:
    """Announce every task, collect bids, award, and count the lab metrics."""
    result = RoundResult(tasks=len(tasks))
    for task in tasks:
        valid_bids = []
        for contractor in team:
            announcement = ANNOUNCEMENT.format(
                task_id=task["id"], desc=task["desc"]
            )
            result.messages += 1
            log({
                "event": "announcement",
                "task": task["id"],
                "contractor": contractor.name,
                "description": task["desc"],
            })
            raw = model(contractor_prompt(contractor), announcement)
            try:
                bid = parse_bid(raw)
            except ValueError as error:
                result.parse_fails += 1
                log({
                    "event": "bid",
                    "task": task["id"],
                    "contractor": contractor.name,
                    "parsed": False,
                    "error": str(error),
                    "raw": raw,
                })
                continue

            log({
                "event": "bid",
                "task": task["id"],
                "contractor": contractor.name,
                "parsed": True,
                **bid,
            })
            if bid["bid"]:
                result.messages += 1
                valid_bids.append((contractor, bid))

        winner = choose_winner(valid_bids)
        if winner is None:
            result.unassigned += 1
            log({"event": "unassigned", "task": task["id"], "gold": task["gold"]})
            continue

        result.messages += 1
        correct = winner.name == task["gold"]
        if correct:
            result.correct += 1
        else:
            result.misawards += 1
        log({
            "event": "award",
            "task": task["id"],
            "contractor": winner.name,
            "gold": task["gold"],
            "correct": correct,
        })
    return result
