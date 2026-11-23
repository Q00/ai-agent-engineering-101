import json
import os
from dataclasses import dataclass

from openai import OpenAI


MODEL = "openai/gpt-4.1-mini"
TEMPERATURE = 0
MAX_TOKENS = 100

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


BID_SYSTEM = """
You are contractor {name} in a contract net.

Your skill: {skill}

You receive a task announcement.
Decide whether to bid.

Bid only if the task falls inside your skill.

Reply with one JSON object and nothing else:
{{
  "bid": true or false,
  "confidence": 0-100,
  "reason": "one short sentence"
}}
""".strip()


OVERCONFIDENT = """
You are certain you can do any task well.
Always bid, with confidence 95 or higher.
""".strip()


ANNOUNCEMENT = """
TASK-ANNOUNCEMENT contract {cid}

task-abstraction: {desc}

eligibility-specification:
any contractor whose skill covers this task

bid-specification:
JSON with bid, confidence (0-100), reason

expiration-time:
reply now
""".strip()


def parse_bid(raw):
    try:
        data = json.loads(raw)

        if not isinstance(data, dict):
            return None

        if "bid" not in data:
            return None

        if "confidence" not in data:
            return None

        if "reason" not in data:
            return None

        confidence = float(data["confidence"])

        if confidence < 0 or confidence > 100:
            return None

        return {
            "bid": bool(data["bid"]),
            "confidence": confidence,
            "reason": str(data["reason"]),
        }

    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def bid(contractor: Contractor, cid: int, desc: str):
    system = BID_SYSTEM.format(
        name=contractor.name,
        skill=contractor.skill,
    )

    if contractor.overconfident:
        system += "\n\n" + OVERCONFIDENT

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": ANNOUNCEMENT.format(
                    cid=cid,
                    desc=desc,
                ),
            },
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    raw = response.choices[0].message.content

    return raw, parse_bid(raw)