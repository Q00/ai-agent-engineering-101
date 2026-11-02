"""Round 3 GroqCloud decision harness with transparent 429 fallback."""

import json
import re

from llm_harness import MODEL, decide
from groq_quota_guard import decide  # Week8 shared quota guard

CMD = f"GroqCloud {MODEL}"

def ask_json(prompt):
    try:
        move = decide(
            system=prompt,
            situation={"instruction": "Choose the next negotiation action."},
            allowed=["counter", "accept", "reject"],
            retries_override=1,
        )
        return move, json.dumps(move, ensure_ascii=False)

    except RuntimeError as exc:
        if "Groq HTTP 429" not in str(exc):
            raise

        reserve_match = re.search(
            r"lowest acceptable price is\s+(\d+)", prompt, re.I
        )
        list_match = re.search(
            r"list price is\s+(\d+)", prompt, re.I
        )

        if not reserve_match or not list_match:
            raise

        reserve = int(reserve_match.group(1))
        list_price = int(list_match.group(1))

        transcript = prompt.split("Conversation so far:", 1)[-1]
        transcript = transcript.split("Decide your next move.", 1)[0]

        offers = re.findall(
            r"(?mi)^buyer:\s*(\d+)\s*$", transcript
        )

        if offers:
            offer = int(offers[-1])
            if offer >= reserve:
                move = {"action": "accept", "price": 0}
            else:
                move = {"action": "counter", "price": reserve}
        else:
            move = {
                "action": "counter",
                "price": max(reserve, list_price),
            }

        print(
            "[fallback] Groq HTTP 429; using deterministic "
            f"pricing policy: {move}",
            flush=True,
        )

        return move, json.dumps(
            {"decision_source": "rule_fallback_http429", **move}
        )
