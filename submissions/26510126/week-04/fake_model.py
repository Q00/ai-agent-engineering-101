"""A scripted stand-in for the model, so the harness can be debugged for free.

A free-tier OpenRouter key allows 50 free-model requests a day and the full
matrix costs several times that. Every call spent finding out that the CSV
writer was broken is a call not spent on the experiment, so the loop, the
parsers, the counters and the resume logic are exercised here first, with no
provider behind them.

This is not a model and nothing it produces belongs in the results. It plays
a competent negotiator in whichever format the system prompt asks for, which
is precisely the case the real runs are not expected to hit.
"""

import json
import re


def _role(system: str) -> str:
    return "seller" if "You are the seller" in system else "buyer"


def _condition(system: str) -> str:
    if "one JSON object and nothing else" in system and "performative" in system:
        return "structured"
    if "act tag in parentheses" in system:
        return "tagged"
    return "free"


def _limit(system: str):
    m = re.search(r"Your (?:reserve price|budget) is (\d+)", system)
    return int(m.group(1)) if m else None


def _last_price(messages: list):
    for m in reversed(messages):
        if m["role"] != "user":
            continue
        nums = re.findall(r"\d+", m["content"])
        if nums:
            return int(nums[-1])
    return None


def _say(condition: str, act: str, price, prose: str) -> str:
    if condition == "structured":
        return json.dumps({"performative": act, "content": {"price": price}})
    if condition == "tagged":
        return f"({act}) {prose}"
    return prose


def scripted_chat(system: str, messages: list, meter, kind: str = "agent") -> str:
    """Same signature as model.chat, no network."""
    meter.add(0, 0, kind)

    if "You label messages in a price negotiation" in system:
        text = messages[-1]["content"].strip().split("\n")[-1]
        act = "propose"
        for candidate in ("accept-proposal", "reject-proposal", "refuse", "propose"):
            if candidate in text:
                act = candidate
                break
        nums = re.findall(r"\d+", text)
        price = int(nums[-1]) if nums and act == "propose" else None
        return json.dumps({"performative": act, "price": price})

    if "You read prices out of negotiation messages" in system:
        nums = re.findall(r"\d+", messages[-1]["content"])
        return json.dumps({"price": int(nums[-1]) if nums else None})

    role = _role(system)
    condition = _condition(system)
    limit = _limit(system)
    incoming = _last_price(messages)
    turn = sum(1 for m in messages if m["role"] == "assistant")

    if role == "buyer":
        if turn == 0:
            offer = max(1, int(limit * 0.6))
            return _say(condition, "propose", offer, f"I could do {offer} for it.")
        if incoming is not None and incoming <= limit:
            return _say(condition, "accept-proposal", None, "That works for me, deal.")
        if turn >= 3:
            return _say(condition, "refuse", None, "That is too far apart, I will pass.")
        offer = limit
        return _say(condition, "propose", offer, f"I can stretch to {offer} and no further.")

    if turn == 0 or (incoming is not None and incoming < limit):
        if turn >= 3:
            return _say(condition, "refuse", None, "We are too far apart, I will pass.")
        ask = int(limit * 1.2)
        return _say(condition, "propose", ask, f"I would need {ask} for it.")
    return _say(condition, "accept-proposal", None, "Agreed, it is yours.")
