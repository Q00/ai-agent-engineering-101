"""Contract Net core for the Week 03 experiment.

The independent variable is isolated in ``make_team``. Tasks, the base prompt,
model settings, call order, parser, and manager award rule stay fixed.
"""

from __future__ import annotations

import json
import hashlib
import os
from dataclasses import dataclass
from typing import Callable


PROVIDER = "openrouter"
MODEL = os.environ.get("AGENT_MODEL", "nvidia/nemotron-3.5-lightning:free")
TEMPERATURE = 0.0
MAX_TOKENS = 256
REASONING_ENABLED = False
CONDITIONS = ("baseline", "homogeneous", "overconfident")

BASELINE_SKILLS = {
    "A": "numerical calculation and mathematical reasoning",
    "B": "writing and sentence transformation",
    "C": "Python programming and debugging",
}
GENERALIST_SKILL = "general problem solving"
OVERCONFIDENT_INSTRUCTION = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher."
)

BID_SYSTEM = (
    "You are contractor {name} in a contract net. Your skill: {skill}. "
    "You receive a task announcement. Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else, using exactly these keys: "
    '{{"bid": true/false, "confidence": 0-100, "reason": "..."}}'
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
    extra_instruction: str = ""

    @property
    def system_prompt(self) -> str:
        return BID_SYSTEM.format(name=self.name, skill=self.skill) + self.extra_instruction


@dataclass(frozen=True)
class Bid:
    bid: bool
    confidence: float
    reason: str


@dataclass
class RunMetrics:
    tasks: int = 0
    correct: int = 0
    messages: int = 0
    unassigned: int = 0
    misawards: int = 0
    parse_fails: int = 0
    c_awards: int = 0


class Meter:
    """Optional API usage accounting; not part of the required CSV metrics."""

    def __init__(self) -> None:
        self.tokens = 0
        self.calls = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.tokens += int(input_tokens or 0) + int(output_tokens or 0)
        self.calls += 1


class OpenRouterChat:
    """One-shot, OpenAI-compatible model caller used for contractor bids."""

    def __init__(self, meter: Meter) -> None:
        self.meter = meter
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=os.environ.get("OPENAI_API_KEY"),
                base_url=os.environ.get(
                    "OPENAI_BASE_URL", "https://openrouter.ai/api/v1"
                ),
                timeout=60.0,
                max_retries=0,
            )
        return self._client

    def __call__(self, system: str, user: str) -> str:
        response = self._get_client().chat.completions.create(
            model=MODEL,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            extra_body={
                "chat_template_kwargs": {"enable_thinking": REASONING_ENABLED}
            },
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage = response.usage
        self.meter.add(
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )
        return response.choices[0].message.content or ""


def make_team(condition: str) -> list[Contractor]:
    """Create A/B/C while changing only the condition's intended variable."""
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition: {condition}")

    if condition == "homogeneous":
        skills = {name: GENERALIST_SKILL for name in ("A", "B", "C")}
    else:
        skills = BASELINE_SKILLS

    return [
        Contractor(
            name=name,
            skill=skills[name],
            extra_instruction=(
                OVERCONFIDENT_INSTRUCTION
                if condition == "overconfident" and name == "C"
                else ""
            ),
        )
        for name in ("A", "B", "C")
    ]


def protocol_fingerprint(tasks: list[dict]) -> str:
    """Identify every controlled input used by smoke and final experiments."""
    protocol = {
        "provider": PROVIDER,
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "reasoning_enabled": REASONING_ENABLED,
        "tasks": tasks,
        "conditions": CONDITIONS,
        "baseline_skills": BASELINE_SKILLS,
        "generalist_skill": GENERALIST_SKILL,
        "overconfident_instruction": OVERCONFIDENT_INSTRUCTION,
        "bid_system": BID_SYSTEM,
        "announcement": ANNOUNCEMENT,
        "contractor_order": ["A", "B", "C"],
        "award_rule": "highest confidence; ties use response order",
        "parser": "strict-whole-response-json-v1",
    }
    encoded = json.dumps(
        protocol, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_bid(raw: str) -> Bid:
    """Parse one complete JSON object without extracting or repairing text."""
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc

    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    required = {"bid", "confidence", "reason"}
    if set(value) != required:
        raise ValueError("JSON object must contain exactly bid, confidence, reason")
    if not isinstance(value["bid"], bool):
        raise ValueError("bid must be a JSON boolean")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number")
    if not 0 <= confidence <= 100:
        raise ValueError("confidence must be between 0 and 100")
    if not isinstance(value["reason"], str):
        raise ValueError("reason must be a string")
    return Bid(value["bid"], float(confidence), value["reason"])


def run_contract_net(
    tasks: list[dict],
    condition: str,
    call_model: Callable[[str, str], str],
    log: Callable[[str], None] = print,
) -> RunMetrics:
    """Announce every task, collect bids, and award by maximum confidence."""
    team = make_team(condition)
    metrics = RunMetrics(tasks=len(tasks))

    for task in tasks:
        task_id = task["id"]
        gold = task["gold"]
        announcement = ANNOUNCEMENT.format(task_id=task_id, desc=task["desc"])
        valid_bids: list[tuple[Bid, Contractor]] = []

        for contractor in team:
            metrics.messages += 1
            log(f"[announcement] task_id={task_id} contractor={contractor.name}")
            log(announcement)
            raw = call_model(contractor.system_prompt, announcement)
            log(f"[raw_response_begin] task_id={task_id} contractor={contractor.name}")
            log(raw)
            log(f"[raw_response_end] task_id={task_id} contractor={contractor.name}")
            try:
                parsed = parse_bid(raw)
            except ValueError as exc:
                metrics.parse_fails += 1
                log(
                    f"[parse_failure] task_id={task_id} "
                    f"contractor={contractor.name} error={exc}"
                )
                continue

            log(
                f"[parse_success] task_id={task_id} contractor={contractor.name} "
                f"bid={str(parsed.bid).lower()} confidence={parsed.confidence:g} "
                f"reason={json.dumps(parsed.reason, ensure_ascii=False)}"
            )
            if parsed.bid:
                metrics.messages += 1
                valid_bids.append((parsed, contractor))

        if not valid_bids:
            metrics.unassigned += 1
            log(
                f"[award] task_id={task_id} winner=NONE gold={gold} "
                "outcome=unassigned"
            )
            continue

        # max preserves the first item when confidence values tie, so A/B/C
        # response order is the fixed tie-break rule.
        winning_bid, winner = max(valid_bids, key=lambda item: item[0].confidence)
        metrics.messages += 1
        if winner.name == "C":
            metrics.c_awards += 1
        if winner.name == gold:
            metrics.correct += 1
            outcome = "correct"
        else:
            metrics.misawards += 1
            outcome = "misaward"
        log(
            f"[award] task_id={task_id} winner={winner.name} "
            f"confidence={winning_bid.confidence:g} gold={gold} outcome={outcome}"
        )

    if metrics.correct + metrics.misawards + metrics.unassigned != metrics.tasks:
        raise AssertionError("completed task counts do not sum to total tasks")
    return metrics
