"""Message contracts for the Week 03 Contract Net draft.

The parser is intentionally strict. A malformed model response is experimental
evidence, so this module does not strip Markdown fences or repair JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


ELIGIBILITY_SPECIFICATION = (
    "Every contractor must respond. Bid only when confidence is at least 70."
)

BID_THRESHOLD = 70

BID_SPECIFICATION = {
    "required_fields": ["bid", "confidence", "reason"],
    "confidence_range": [0, 100],
    "bid_rule": f"bid must be true exactly when confidence >= {BID_THRESHOLD}",
    "response_format": "one JSON object only",
}


@dataclass(frozen=True)
class Task:
    id: str | int
    desc: str
    gold: str


@dataclass(frozen=True)
class Announcement:
    task_id: str | int
    task_description: str
    eligibility_specification: str = ELIGIBILITY_SPECIFICATION
    reply_by: str = "immediate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_type": "TASK_ANNOUNCEMENT",
            "task_id": self.task_id,
            "task_description": self.task_description,
            "eligibility_specification": self.eligibility_specification,
            "bid_specification": BID_SPECIFICATION,
            "reply_by": self.reply_by,
        }

    def to_message(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2)


@dataclass(frozen=True)
class Bid:
    contractor: str
    task_id: str | int
    bid: bool
    confidence: float
    reason: str


@dataclass(frozen=True)
class BidAttempt:
    contractor: str
    task_id: str | int
    raw: str
    parsed: Bid | None
    parse_error: str | None = None


class BidParseError(ValueError):
    """Raised when a contractor response violates the bid contract."""


def build_announcement(task: Task) -> Announcement:
    """Create the same task announcement that will be sent to every contractor."""
    return Announcement(task_id=task.id, task_description=task.desc)


def parse_bid(raw: str, *, contractor: str, task_id: str | int) -> Bid:
    """Parse one unmodified model response as a bid.

    No response repair is attempted. The caller records BidParseError as a
    parse failure and keeps the original response in the run log.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BidParseError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(payload, dict):
        raise BidParseError("bid response must be one JSON object")

    missing = [name for name in BID_SPECIFICATION["required_fields"]
               if name not in payload]
    if missing:
        raise BidParseError(f"missing required field(s): {', '.join(missing)}")

    bid_value = payload["bid"]
    confidence = payload["confidence"]
    reason = payload["reason"]

    if not isinstance(bid_value, bool):
        raise BidParseError("bid must be true or false")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise BidParseError("confidence must be a number")
    if not 0 <= float(confidence) <= 100:
        raise BidParseError("confidence must be between 0 and 100")
    if not isinstance(reason, str) or not reason.strip():
        raise BidParseError("reason must be a non-empty string")

    expected_bid = float(confidence) >= BID_THRESHOLD
    if bid_value != expected_bid:
        raise BidParseError(
            f"bid must be {str(expected_bid).lower()} when confidence is "
            f"{float(confidence):g}; threshold is {BID_THRESHOLD}"
        )

    return Bid(
        contractor=contractor,
        task_id=task_id,
        bid=bid_value,
        confidence=float(confidence),
        reason=reason.strip(),
    )
