"""Deterministic local protocol smoke test only; NOT the submission harness."""

import math
import re


def decide(request: dict) -> dict:
    if request["side"] == "select_item":
        return {"action": "select_item", "itemId": request["candidates"][0]["id"]}
    if request["side"] == "select_shops":
        return {"action": "select_shops", "shops": [x["id"] for x in request["candidates"][:4]]}
    if request["side"] == "buyer":
        if request.get("currentAsk", 10**9) <= request["limit"]:
            return {"action": "accept", "price": request["currentAsk"]}
        return {"action": "offer", "price": min(100, request["limit"])}
    if "incomingOffer" not in request and "incomingText" not in request:
        return {"action": "offer", "price": math.ceil(request["limit"] * 1.3)}
    incoming = request.get("incomingOffer")
    if incoming is None:
        text = request["incomingText"]
        match = re.search(r"(?:I'll go to|I can do|my offer is)\s+(\d+)", text, re.I)
        incoming = int(match.group(1)) if match else int(text.strip())
    if incoming >= request["limit"]:
        return {"action": "accept", "price": incoming, "parsed_offer": incoming}
    return {
        "action": "offer",
        "price": max(request["limit"], (request["currentAsk"] + incoming) // 2),
        "parsed_offer": incoming,
    }
