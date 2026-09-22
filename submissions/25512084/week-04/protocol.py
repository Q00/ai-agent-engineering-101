import json
import re

from model import Meter, call_model


ALLOWED = {
    "propose",
    "accept-proposal",
    "reject-proposal",
    "refuse",
}

READER_SYSTEM = """
You are a protocol reader for a price negotiation.

Classify the message into exactly one of these performatives:
propose, accept-proposal, reject-proposal, refuse.

Also extract the price if there is one.

Reply with exactly one JSON object:
{"performative": "...", "price": integer_or_null}

Use propose when the message offers or counter-offers a specific price.
Use accept-proposal only when it clearly accepts the other side's last offer.
Use reject-proposal when it rejects the current offer but continues negotiation.
Use refuse when it leaves or ends negotiation without a deal.
"""


def llm_read(message, meter: Meter):
    raw = call_model(
        system=READER_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"Read this negotiation message:\n{message}",
            }
        ],
        meter=meter,
        temperature=0,
    )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None, raw

    if not isinstance(data, dict):
        return None, raw

    performative = data.get("performative")
    price = data.get("price")

    if performative not in ALLOWED:
        return None, raw

    if price is not None and not isinstance(price, int):
        return None, raw

    return {
        "performative": performative,
        "price": price,
    }, raw


def read_free(message, meter: Meter):
    parsed, raw = llm_read(message, meter)
    return parsed, raw, 1


def read_tagged(message, meter: Meter):
    match = re.match(
        r"^\s*\((propose|accept-proposal|reject-proposal|refuse)\)",
        message,
        re.IGNORECASE,
    )

    if not match:
        return None, "tag parse failed", 0

    performative = match.group(1).lower()

    if performative != "propose":
        return {
            "performative": performative,
            "price": None,
        }, "tag parsed", 0

    parsed, raw = llm_read(message, meter)

    if parsed is None:
        return None, raw, 1

    return {
        "performative": performative,
        "price": parsed.get("price"),
    }, raw, 1


def read_structured(message, meter: Meter):
    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return None, "json parse failed", 0

    if not isinstance(data, dict):
        return None, "not a JSON object", 0

    performative = data.get("performative")
    content = data.get("content")

    if performative not in ALLOWED:
        return None, "invalid performative", 0

    if not isinstance(content, dict):
        return None, "invalid content", 0

    price = content.get("price")

    if performative == "propose":
        if not isinstance(price, int):
            return None, "propose requires integer price", 0
    else:
        if price is not None and not isinstance(price, int):
            return None, "invalid price", 0

    return {
        "performative": performative,
        "price": price,
    }, "json parsed", 0


def read_message(condition, message, meter: Meter):
    if condition == "free":
        return read_free(message, meter)

    if condition == "tagged":
        return read_tagged(message, meter)

    if condition == "structured":
        return read_structured(message, meter)

    raise ValueError(f"unknown condition: {condition}")
