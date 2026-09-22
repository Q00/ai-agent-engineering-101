"""Week 03 — Contract Net core implementation.

One manager announces each delivery-platform task to three LLM contractors.
Each contractor decides whether to bid and returns a confidence score.
The manager awards the task to the highest-confidence bidder.
"""

import json
import os
from dataclasses import dataclass
from typing import Callable

from openai import OpenAI


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

PROVIDER = os.environ.get(
    "AGENT_PROVIDER",
    "OpenRouter",
)

MODEL = os.environ.get(
    "AGENT_MODEL",
    "inclusionai/ling-3.0-flash-vl:free",
)

TEMPERATURE = 0
MAX_TOKENS = 300

_client = None


def get_client() -> OpenAI:
    """Create the OpenAI-compatible OpenRouter client when first needed."""
    global _client

    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get(
            "OPENAI_BASE_URL",
            "https://openrouter.ai/api/v1",
        )

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    return _client


# ---------------------------------------------------------------------------
# Experiment data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


@dataclass
class RoundResult:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    tokens: int = 0


BASELINE_SKILLS = {
    "A": (
        "Delivery-platform order analytics, cancellation-rate analysis, "
        "discount calculations, refund calculations, and pricing metrics."
    ),
    "B": (
        "Customer and restaurant-partner communication, delay notices, "
        "apology messages, and service-incident announcements."
    ),
    "C": (
        "Python backend development, coupon validation, API retry logic, "
        "order-system debugging, and software implementation."
    ),
}

GENERALIST_SKILL = (
    "General problem solving across analysis, communication, and programming."
)

BID_SYSTEM = (
    "You are contractor {name} in a contract net. "
    "Your specialty is: {skill} "
    "Decide whether the announced task belongs to your specialty. "
    "Do not solve the task. Do not repeat the task or these instructions. "
    "Do not explain your reasoning outside the JSON. "
    "Output exactly one compact JSON object on one line. "
    "Use exactly three keys: bid, confidence, reason. "
    "bid must be true or false. "
    "confidence must be a number from 0 to 100. "
    "reason must be at most eight words and must not contain embedded quotation marks."
)

OVERCONFIDENT_PROMPT = (
    " You are certain that you can handle every task well. "
    "Always bid, with confidence 95 or higher."
)


# ---------------------------------------------------------------------------
# Team and message construction
# ---------------------------------------------------------------------------


def build_team(condition: str) -> list[Contractor]:
    """Build A, B, and C while changing only the requested condition."""

    if condition == "baseline":
        return [
            Contractor("A", BASELINE_SKILLS["A"]),
            Contractor("B", BASELINE_SKILLS["B"]),
            Contractor("C", BASELINE_SKILLS["C"]),
        ]

    if condition == "homogeneous":
        return [
            Contractor("A", GENERALIST_SKILL),
            Contractor("B", GENERALIST_SKILL),
            Contractor("C", GENERALIST_SKILL),
        ]

    if condition == "overconfident":
        return [
            Contractor("A", BASELINE_SKILLS["A"]),
            Contractor("B", BASELINE_SKILLS["B"]),
            Contractor("C", BASELINE_SKILLS["C"], overconfident=True),
        ]

    raise ValueError(f"unknown condition: {condition}")


def make_announcement(task: dict) -> str:
    """Construct the task announcement without exposing the gold answer."""

    return (
        f"TASK-ANNOUNCEMENT contract {task['id']}\n"
        f"task-abstraction: {task['desc']}\n"
        "eligibility-specification: any contractor whose specialty covers "
        "this task\n"
        "bid-specification: JSON with bid, confidence (0-100), and reason\n"
        "expiration-time: reply now"
    )


# ---------------------------------------------------------------------------
# Bid generation and validation
# ---------------------------------------------------------------------------


def parse_bid(raw: str) -> dict | None:
    """Validate a contractor response.

    An invalid or non-JSON response is treated as no bid and counted as a
    parse failure by the manager.
    """

    try:
        value = json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError):
        return None

    if not isinstance(value, dict):
        return None

    bid = value.get("bid")
    confidence = value.get("confidence")
    reason = value.get("reason")

    if not isinstance(bid, bool):
        return None

    # bool is a subclass of int in Python, so exclude it explicitly.
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None

    if not 0 <= confidence <= 100:
        return None

    if not isinstance(reason, str):
        return None

    return {
        "bid": bid,
        "confidence": float(confidence),
        "reason": reason.strip(),
    }


def request_bid(
    contractor: Contractor,
    task: dict,
) -> tuple[dict | None, str, int]:
    """Ask one contractor for one bid.

    Returns:
        parsed bid or None,
        raw model response,
        number of tokens used.
    """

    system_prompt = BID_SYSTEM.format(
        name=contractor.name,
        skill=contractor.skill,
    )

    if contractor.overconfident:
        system_prompt += OVERCONFIDENT_PROMPT

    announcement = make_announcement(task)

    response = get_client().chat.completions.create(
        model=MODEL,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": announcement,
            },
        ],
        response_format={
            "type": "json_object",
        },
        extra_body={
            "include_reasoning": False,
        },
    )

    if not response.choices:
        raise RuntimeError("model response contained no choices")

    raw = response.choices[0].message.content or ""

    usage = response.usage
    tokens = 0
    if usage is not None:
        tokens = int(usage.prompt_tokens or 0) + int(
            usage.completion_tokens or 0
        )

    return parse_bid(raw), raw, tokens


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


def run_round(
    tasks: list[dict],
    condition: str,
    log: Callable[[str], None] = print,
) -> RoundResult:
    """Run one complete Contract Net round over every task."""

    team = build_team(condition)
    result = RoundResult(tasks=len(tasks))

    for task in tasks:
        log("")
        log(f"[task] id={task['id']} gold={task['gold']}")
        log(f"[description] {task['desc']}")

        bids = []

        for order, contractor in enumerate(team):
            announcement = make_announcement(task)

            # One announcement sent to each contractor.
            result.messages += 1
            log(
                f"[announcement] task={task['id']} "
                f"contractor={contractor.name}"
            )
            log(announcement)

            parsed, raw, tokens = request_bid(contractor, task)
            result.tokens += tokens

            if parsed is None:
                result.parse_fails += 1
                log(
                    f"[parse-fail] contractor={contractor.name} "
                    f"raw={raw!r}"
                )
                continue

            log(
                f"[bid] contractor={contractor.name} "
                f"bid={str(parsed['bid']).lower()} "
                f"confidence={parsed['confidence']:.1f} "
                f"reason={parsed['reason']}"
            )

            if parsed["bid"] is True:
                # A submitted bid is one message.
                result.messages += 1
                bids.append(
                    (
                        parsed["confidence"],
                        order,
                        contractor,
                        parsed["reason"],
                    )
                )

        if not bids:
            result.unassigned += 1
            log(
                f"[award] task={task['id']} winner=NONE "
                f"gold={task['gold']} unassigned=true"
            )
            continue

        # max() returns the first item when confidence values are tied,
        # preserving contractor order A -> B -> C.
        winner_bid = max(bids, key=lambda item: item[0])
        confidence, _, winner, reason = winner_bid

        # One award notification.
        result.messages += 1

        correct = winner.name == task["gold"]

        if correct:
            result.correct += 1
        else:
            result.misawards += 1

        log(
            f"[award] task={task['id']} "
            f"winner={winner.name} "
            f"gold={task['gold']} "
            f"confidence={confidence:.1f} "
            f"correct={str(correct).lower()} "
            f"reason={reason}"
        )

    return result