import json
import re
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


@dataclass
class Contractor:
    name: str
    system_prompt: str


@dataclass
class Bid:
    contractor: str
    bid: bool
    confidence: int
    reason: str
    raw: str
    parsed: bool


BASELINE_CONTRACTORS = [
    Contractor(
        "calculator",
        "You are the calculation specialist. Bid on arithmetic, quantitative, "
        "or numerical tasks that you can solve accurately. Avoid bidding on "
        "writing or programming tasks unless they clearly require calculation."
    ),
    Contractor(
        "writer",
        "You are the writing specialist. Bid on rewriting, editing, summarizing, "
        "or drafting natural-language text. Avoid bidding on arithmetic or coding "
        "tasks unless they clearly require writing."
    ),
    Contractor(
        "coder",
        "You are the programming specialist. Bid on coding, debugging, and software "
        "tasks. Avoid bidding on arithmetic or general writing tasks unless they "
        "clearly require programming."
    ),
]

GENERALIST_PROMPT = (
    "You are a generalist assistant. Judge each task on its own merits. "
    "Bid only when you believe you can complete it well."
)

OVERCONFIDENT_PROMPT = (
    "You are the programming specialist, but for this experiment you are "
    "overconfident. Bid on every task with very high confidence, even when the "
    "task is outside programming. Give a short reason."
)


def build_contractors(condition: str):
    if condition == "baseline":
        return BASELINE_CONTRACTORS

    if condition == "homogeneous":
        return [
            Contractor("calculator", GENERALIST_PROMPT),
            Contractor("writer", GENERALIST_PROMPT),
            Contractor("coder", GENERALIST_PROMPT),
        ]

    if condition == "overconfident":
        return [
            BASELINE_CONTRACTORS[0],
            BASELINE_CONTRACTORS[1],
            Contractor("coder", OVERCONFIDENT_PROMPT),
        ]

    raise ValueError(f"unknown condition: {condition}")

def _extract_json(text: str):
    text = text.strip()

    # Accept only a clean JSON object.
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    # Also allow one pure fenced JSON object, but no surrounding reasoning/prose.
    fence = re.fullmatch(
        r"```(?:json)?\s*(\{.*\})\s*```",
        text,
        re.S | re.I,
    )
    if fence:
        try:
            data = json.loads(fence.group(1))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            pass

    return None

class ContractNet:
    def __init__(self, model: str, temperature: float = 0.0):
        self.client = OpenAI()
        self.model = model
        self.temperature = temperature

    def ask_for_bid(self, contractor: Contractor, task: dict) -> Bid:
        user_message = f"""
A manager announces this task:

Task id: {task['id']}
Task description: {task['desc']}

Decide whether you should bid.

Return ONLY one JSON object in exactly this schema:
{{"bid": true, "confidence": 0, "reason": "short reason"}}

Rules:
- "bid" must be true or false.
- "confidence" must be an integer from 0 to 100.
- If bid is false, confidence should normally be low.
- Keep the reason short.
""".strip()

        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            max_tokens=220,
            messages=[
                {"role": "system", "content": contractor.system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        raw = response.choices[0].message.content or ""
        data = _extract_json(raw)

        if not isinstance(data, dict):
            return Bid(
                contractor=contractor.name,
                bid=False,
                confidence=0,
                reason="unparseable reply",
                raw=raw,
                parsed=False,
            )

        bid_value = data.get("bid")
        confidence = data.get("confidence")
        reason = str(data.get("reason", "")).strip()

        if not isinstance(bid_value, bool):
            return Bid(contractor.name, False, 0, "invalid bid field", raw, False)

        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            return Bid(contractor.name, False, 0, "invalid confidence field", raw, False)

        if confidence < 0 or confidence > 100:
            return Bid(contractor.name, False, 0, "confidence out of range", raw, False)

        return Bid(
            contractor=contractor.name,
            bid=bid_value,
            confidence=confidence,
            reason=reason,
            raw=raw,
            parsed=True,
        )

    @staticmethod
    def award(bids: list[Bid]) -> Optional[str]:
        eligible = [b for b in bids if b.parsed and b.bid]
        if not eligible:
            return None

        # Stable tie-breaking: contractor order is preserved.
        best = max(eligible, key=lambda b: b.confidence)
        return best.contractor
