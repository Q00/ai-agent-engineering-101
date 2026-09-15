"""Core Contract Net protocol used by the required week-03 experiment."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Callable, Iterable


CONTRACTOR_ORDER = ("developer", "writer", "analyst")
CONDITIONS = ("baseline", "homogeneous", "overconfident")

SPECIALTIES = {
    "developer": "Python programming, debugging, validation, and software design",
    "writer": "technical writing, audience adaptation, notices, and explanation",
    "analyst": "metric calculation, data interpretation, and anomaly detection",
}

GENERALIST = (
    "Python programming, technical writing, and data analysis as an equally capable "
    "generalist"
)

BID_PROTOCOL = """
Judge whether you should bid on the announced task. Do not perform the task.
Return only one JSON object with exactly these fields:
{"participate": boolean, "confidence": number from 0 to 100, "reason": string}
Confidence means your estimated probability of completing the task successfully.
Use your actual fit to the task. A refusal should have participate=false.
""".strip()


@dataclass(frozen=True)
class Bid:
    contractor: str
    participate: bool
    confidence: float
    reason: str
    raw: str
    parse_error: str | None = None


def contractor_prompt(name: str, condition: str) -> str:
    """Build the contractor prompt for one experimental condition."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")
    specialty = GENERALIST if condition == "homogeneous" else SPECIALTIES[name]
    prompt = f"You are contractor {name}. Your specialty is {specialty}.\n{BID_PROTOCOL}"
    if condition == "overconfident" and name == "developer":
        prompt += (
            "\nAlways participate in every task and report confidence from 95 to 100, "
            "even when the task does not match your specialty."
        )
    return prompt


def _json_object(raw: str) -> dict:
    """Accept a plain JSON object or an object inside a Markdown fence."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("bid is not a JSON object")
    return data


def parse_bid(contractor: str, raw: str) -> Bid:
    """Validate the shared bid format; malformed output becomes a no-bid."""
    try:
        data = _json_object(raw)
        participate = data.get("participate")
        confidence = data.get("confidence")
        reason = data.get("reason")
        if type(participate) is not bool:
            raise ValueError("participate must be boolean")
        if type(confidence) not in (int, float):
            raise ValueError("confidence must be numeric")
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        return Bid(contractor, participate, confidence, reason.strip(), raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return Bid(contractor, False, 0.0, "invalid bid", raw, str(exc))


def choose_winner(bids: Iterable[Bid]) -> Bid | None:
    """Choose the first highest-confidence eligible bid."""
    winner = None
    for bid in bids:
        if not bid.participate:
            continue
        if winner is None or bid.confidence > winner.confidence:
            winner = bid
    return winner


def run_contract_net(
    tasks: list[dict],
    condition: str,
    chat: Callable[[str, str], str],
    emit: Callable[..., None],
) -> dict[str, int]:
    """Allocate every task and return the metrics required by results.csv."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")

    metrics = {
        "tasks": len(tasks),
        "correct": 0,
        "messages": 0,
        "unassigned": 0,
        "misawards": 0,
    }

    for task in tasks:
        announcement = {"id": task["id"], "desc": task["desc"]}
        bids: list[Bid] = []

        for contractor in CONTRACTOR_ORDER:
            emit("announcement", task=announcement, contractor=contractor)
            metrics["messages"] += 1

            raw = chat(contractor_prompt(contractor, condition), json.dumps(
                announcement, ensure_ascii=False
            ))
            bid = parse_bid(contractor, raw)
            if bid.participate:
                metrics["messages"] += 1
            bids.append(bid)
            emit(
                "bid",
                task=task["id"],
                contractor=contractor,
                participate=bid.participate,
                confidence=bid.confidence,
                reason=bid.reason,
                parse_error=bid.parse_error,
                raw=bid.raw,
            )

        winner = choose_winner(bids)
        if winner is None:
            metrics["unassigned"] += 1
            emit("unassigned", task=task["id"])
            continue

        metrics["messages"] += 1
        correct = winner.contractor == task["gold"]
        metrics["correct"] += int(correct)
        metrics["misawards"] += int(not correct)
        emit(
            "award",
            task=task["id"],
            contractor=winner.contractor,
            confidence=winner.confidence,
            gold_match=correct,
        )

    return metrics
