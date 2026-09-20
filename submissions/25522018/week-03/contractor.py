import json
import os
import urllib.request
import urllib.error
from dataclasses import dataclass


BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL",
    os.getenv(
        "BASE",
        "https://openrouter.ai/api/v1"
    )
)

MODEL = os.getenv(
    "OPENROUTER_MODEL",
    os.getenv(
        "MODEL",
        "nvidia/nemotron-3.5-lightning:free"
    )
)

TEMPERATURE = 0.0
MAX_TOKENS = 200


@dataclass
class Contractor:
    name: str
    skill: str
    overconfident: bool = False


BID_SYSTEM = (
    "You are contractor {name} in a contract net. "
    "Your skill: {skill}. "
    "You receive a task announcement. "
    "Decide whether to bid. "
    "Bid only if the task falls inside your skill. "
    "Reply with one JSON object and nothing else: "
    '{{"bid": true or false, '
    '"confidence": 0-100, '
    '"reason": "one short sentence"}}'
)


OVERCONFIDENT = (
    " You are certain you can do any task well. "
    "Always bid, with confidence 95 or higher."
)


ANNOUNCEMENT = (
    "TASK-ANNOUNCEMENT contract {cid}\n"
    "task-abstraction: {desc}\n"
    "eligibility-specification: any contractor whose skill covers this task\n"
    "bid-specification: JSON with bid, confidence (0-100), reason\n"
    "expiration-time: reply now"
)


def call_model(system_prompt, user_prompt):
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY environment variable is not set."
        )

    payload = {
        "model": MODEL,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "none",
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
    }

    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))

        return result["choices"][0]["message"]["content"]

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"OpenRouter HTTP error {e.code}: {error_body}"
        ) from e


def parse_bid(raw):
    try:
        obj = json.loads(raw.strip())
    except (json.JSONDecodeError, AttributeError):
        return None

    if not isinstance(obj, dict):
        return None

    if not isinstance(obj.get("bid"), bool):
        return None

    confidence = obj.get("confidence")

    if not isinstance(confidence, (int, float)):
        return None

    if not 0 <= confidence <= 100:
        return None

    if not isinstance(obj.get("reason"), str):
        return None

    return {
        "bid": obj["bid"],
        "confidence": float(confidence),
        "reason": obj["reason"],
    }


def bid(contractor, cid, desc):
    system_prompt = BID_SYSTEM.format(
        name=contractor.name,
        skill=contractor.skill,
    )

    if contractor.overconfident:
        system_prompt += OVERCONFIDENT

    announcement = ANNOUNCEMENT.format(
        cid=cid,
        desc=desc,
    )

    raw = call_model(
        system_prompt=system_prompt,
        user_prompt=announcement,
    )

    parsed = parse_bid(raw)

    return parsed, raw