from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI


MODEL = os.getenv(
    "AGENT_MODEL",
    "openai/gpt-oss-120b",
)

BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://api.groq.com/openai/v1",
)

API_KEY = os.getenv("OPENAI_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "OPENAI_API_KEY is not set."
    )


client = OpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    try:
        value = json.loads(text)

        if isinstance(value, dict):
            return value

    except json.JSONDecodeError:
        pass

    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    if fenced:
        value = json.loads(
            fenced.group(1)
        )

        if isinstance(value, dict):
            return value

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        value = json.loads(
            text[start:end + 1]
        )

        if isinstance(value, dict):
            return value

    raise ValueError(
        "Model did not return a valid JSON object. "
        f"Raw response: {text!r}"
    )


RATE_LIMIT_FILE = Path('.market') / 'groq-cooldown.json'


def _seconds_until_model_available() -> float:
    try:
        state = json.loads(RATE_LIMIT_FILE.read_text(encoding='utf-8'))
        return max(0.0, float(state.get('until', 0)) - time.time())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def _register_quota_cooldown(error: Exception) -> bool:
    if (getattr(error, 'status_code', None) != 429
            and 'rate_limit_exceeded' not in str(error)
            and 'Rate limit reached' not in str(error)):
        return False
    message = str(error)
    match = re.search(r'try again in\s*(?:(\d+)m)?\s*([\d.]+)s', message, re.I)
    seconds = (int(match.group(1) or 0) * 60 + float(match.group(2))) if match else 900.0
    until = time.time() + max(30.0, min(seconds + 20.0, 3600.0))
    try:
        RATE_LIMIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RATE_LIMIT_FILE.write_text(json.dumps({'until': until}), encoding='utf-8')
    except OSError:
        pass
    print(f'[GROQ RATE LIMIT] API returned 429; pause model calls for ~{int(seconds)} seconds.', flush=True)
    return True


def _call_json(
    system_prompt: str,
    user_prompt: str,
    label: str,
) -> dict[str, Any]:
    """
    Call GroqCloud with retries.

    Retries handle:
    - temporary API/provider failures
    - malformed JSON
    - empty model responses
    """

    last_error: Exception | None = None

    remaining = _seconds_until_model_available()
    if remaining > 0:
        raise RuntimeError(f'Groq cooldown active ({int(remaining)}s remaining after 429 rate limit)')

    for attempt in range(1, 6):

        try:
            response = (
                client.chat.completions.create(
                    model=MODEL,
                    temperature=0,
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": user_prompt,
                        },
                    ],
                )
            )

            text = (
                response.choices[0]
                .message.content
                or ""
            )

            result = _extract_json(text)

            return result

        except Exception as exc:
            last_error = exc
            if _register_quota_cooldown(exc):
                raise RuntimeError('Groq API 429 rate limit; cooldown active') from exc

            print(
                "[HARNESS RETRY] "
                f"label={label} "
                f"attempt={attempt}/5 "
                f"error={type(exc).__name__}: "
                f"{exc}",
                flush=True,
            )

            if attempt < 5:
                time.sleep(
                    min(
                        2.0,
                        0.5 * attempt,
                    )
                )

    raise RuntimeError(
        f"Harness failed after 5 attempts "
        f"for {label}: {last_error}"
    )


def seller_opening(
    *,
    item: str,
    reserve: int,
) -> dict[str, Any]:

    system = """
You are the decision-making harness for an A2A seller.

You decide the opening asking price.

Rules:
- The reserve is the absolute minimum selling price.
- Never ask below the reserve.
- Choose a realistic whole-number opening price.
- Leave some room for negotiation.
- You must make the pricing decision.

Return ONLY JSON in exactly this form:
{"action":"ask","price":INTEGER}
""".strip()

    user = f"""
Item: {item}
Seller reserve: {reserve}

Choose the opening asking price.
""".strip()

    last_result = None

    for semantic_attempt in range(1, 4):

        result = _call_json(
            system,
            user,
            label="seller_opening",
        )

        last_result = result

        try:
            if (
                str(
                    result.get(
                        "action",
                        "",
                    )
                ).lower()
                != "ask"
            ):
                raise ValueError(
                    "action must be ask"
                )

            price = int(
                result["price"]
            )

            # Hard reserve enforcement.
            price = max(
                reserve,
                price,
            )

            return {
                "action": "ask",
                "price": price,
            }

        except Exception as exc:

            print(
                "[HARNESS SEMANTIC RETRY] "
                f"seller_opening "
                f"attempt={semantic_attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Seller opening decision remained "
        f"invalid: {last_result}"
    )


def seller_turn(
    *,
    item: str,
    reserve: int,
    current_ask: int,
    buyer_offer: int,
    turn_number: int,
    history: list[str],
) -> dict[str, Any]:

    system = """
You are the decision-making harness for an A2A seller.

You decide whether to accept, counteroffer, or reject.

Rules:
- The reserve is the absolute minimum selling price.
- Never accept an offer below the reserve.
- Never counter below the reserve.
- If the buyer offers at least the reserve, accepting is normally best.
- If the buyer is below reserve, negotiate with a counteroffer at or above reserve.
- Do not reject too early when progress is being made.
- Keep negotiation practical and short.

Return ONLY one JSON object:

{"action":"accept"}

or

{"action":"counter","price":INTEGER}

or

{"action":"reject"}
""".strip()

    user = f"""
Item: {item}
Seller reserve: {reserve}
Current asking price: {current_ask}
Buyer's latest offer: {buyer_offer}
Negotiation turn: {turn_number}

History:
{json.dumps(history)}

Choose the seller's next action.
""".strip()

    last_result = None

    for semantic_attempt in range(1, 4):

        result = _call_json(
            system,
            user,
            label="seller_turn",
        )

        last_result = result

        try:
            action = str(
                result.get(
                    "action",
                    "",
                )
            ).lower()

            if action == "accept":

                if buyer_offer < reserve:
                    raise ValueError(
                        "cannot accept below reserve"
                    )

                return {
                    "action": "accept",
                }

            if action == "counter":

                price = int(
                    result["price"]
                )

                price = max(
                    reserve,
                    price,
                )

                return {
                    "action": "counter",
                    "price": price,
                }

            if action == "reject":

                return {
                    "action": "reject",
                }

            raise ValueError(
                f"unknown action: {action}"
            )

        except Exception as exc:

            print(
                "[HARNESS SEMANTIC RETRY] "
                f"seller_turn "
                f"attempt={semantic_attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Seller decision remained invalid: "
        f"{last_result}"
    )


def buyer_turn(
    *,
    item: str,
    budget: int,
    seller_price: int,
    turn_number: int,
    history: list[str],
) -> dict[str, Any]:

    system = """
You are the decision-making harness for an A2A buyer.

You decide every buyer offer and when to walk away.

Rules:
- The budget is the absolute maximum purchase price.
- Never offer more than the budget.
- Your goal is to buy when the seller can reach the budget.
- If the seller's price is already within budget, normally offer that price to close.
- If the seller's price is above budget, negotiate upward toward the budget.
- Do not walk away too early when a deal is still possible.
- Walk away when the seller cannot reach the budget.
- All offers must be whole numbers.

Return ONLY one JSON object:

{"action":"offer","price":INTEGER}

or

{"action":"walk"}
""".strip()

    user = f"""
Item: {item}
Buyer budget: {budget}
Seller's latest asking or counter price: {seller_price}
Negotiation turn: {turn_number}

History:
{json.dumps(history)}

Choose the buyer's next action.
""".strip()

    last_result = None

    for semantic_attempt in range(1, 4):

        result = _call_json(
            system,
            user,
            label="buyer_turn",
        )

        last_result = result

        try:
            action = str(
                result.get(
                    "action",
                    "",
                )
            ).lower()

            if action == "offer":

                price = int(
                    result["price"]
                )

                # Hard budget enforcement.
                price = min(
                    budget,
                    price,
                )

                price = max(
                    0,
                    price,
                )

                return {
                    "action": "offer",
                    "price": price,
                }

            if action == "walk":

                return {
                    "action": "walk",
                }

            raise ValueError(
                f"unknown action: {action}"
            )

        except Exception as exc:

            print(
                "[HARNESS SEMANTIC RETRY] "
                f"buyer_turn "
                f"attempt={semantic_attempt}/3 "
                f"result={result} "
                f"error={exc}",
                flush=True,
            )

    raise RuntimeError(
        "Buyer decision remained invalid: "
        f"{last_result}"
    )


if __name__ == "__main__":

    print(
        json.dumps(
            {
                "provider": "GroqCloud",
                "model": MODEL,
                "base_url": BASE_URL,
                "status": "ready",
            },
            indent=2,
        )
    )