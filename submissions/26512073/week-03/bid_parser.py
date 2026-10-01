import json
import math


def parse_bid(raw):
    """Return (valid bid, None), or (None, explanation)."""
    try:
        bid = json.loads(raw)
    except json.JSONDecodeError:
        return None, "Invalid JSON"

    if not isinstance(bid, dict):
        return None, "Expected a JSON object"

    if type(bid.get("bid")) is not bool:
        return None, "bid must be true or false"

    confidence = bid.get("confidence")
    if type(confidence) not in (int, float):
        return None, "confidence must be a number"

    if not math.isfinite(confidence) or not 0 <= confidence <= 100:
        return None, "confidence must be between 0 and 100"

    if not isinstance(bid.get("reason"), str):
        return None, "reason must be text"

    return bid, None


if __name__ == "__main__":
    valid = '{"bid": true, "confidence": 95, "reason": "My skill matches."}'
    assert parse_bid(valid)[0]["confidence"] == 95
    assert parse_bid("Not JSON")[0] is None
    assert parse_bid('{"bid": "yes"}')[0] is None
    print("All 3 bid-parser checks passed.")