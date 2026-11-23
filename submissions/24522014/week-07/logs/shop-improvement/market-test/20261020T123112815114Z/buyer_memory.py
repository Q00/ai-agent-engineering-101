"""Persistent observations and the student's explicit shop-selection rules."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re

RECENT_COUNT = 5  # Initial setting, not a measured optimum.
ERROR_COOLDOWN_SECONDS = 3600


def load_memory(path):
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        if not isinstance(record, dict) or record.get("version") != 1:
            raise ValueError(f"Invalid memory record at line {number}")
        # Fail visibly rather than silently forgetting corrupted history.
        for field in ("shop_name", "item", "outcome", "timestamp"):
            if not isinstance(record.get(field), str):
                raise ValueError(f"Invalid memory {field} at line {number}")
        datetime.fromisoformat(record["timestamp"]).astimezone(timezone.utc)
        records.append(record)
    return records


def remember(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"version": 1, **record}, ensure_ascii=False, allow_nan=False) + "\n")


def repeated_price_complaints(reviews):
    complaints = set()
    for review in reviews:
        stars = review.get("stars")
        text = str(review.get("review", "")).casefold()
        if type(stars) not in (int, float) or not 1 <= stars <= 2:
            continue
        prices = re.search(r"agreed\s+(\d+(?:\.\d+)?).*receipt\s+(?:said|says)\s+(\d+(?:\.\d+)?)", text)
        english = ("receipt" in text and (any(word in text for word in
                   ("higher", "mismatch", "overcharg", "different"))
                   or bool(prices and float(prices[1]) != float(prices[2]))))
        korean = ("영수증" in text and any(word in text for word in
                  ("다르", "불일치", "과다", "높")))
        if english or korean:
            complaints.add(text.strip())
    return len(complaints) >= 2


def choose_shop(shops, memory, item, budget, *, now=None, recent_count=RECENT_COUNT):
    """Rank observable evidence; never infer dishonesty from a no-deal or outage."""
    now = now or datetime.now(timezone.utc)
    candidates, evidence = [], []
    for shop in shops:
        history = [r for r in memory if r["shop_name"] == shop["name"]]
        blocked = any(r["outcome"] == "receipt_mismatch" for r in history)
        recent = history[-recent_count:]
        errors = 0
        for record in reversed(recent):
            if record["outcome"] == "purchased":
                break
            age = (now - datetime.fromisoformat(record["timestamp"])).total_seconds()
            if record["outcome"] == "transport_error" and 0 <= age <= ERROR_COOLDOWN_SECONDS:
                errors += 1
        successes = [r for r in history if r["item"] == item and r["outcome"] == "purchased"
                     and type(r.get("price")) in (int, float)
                     and math.isfinite(r["price"]) and 0 < r["price"] <= budget]
        warning = repeated_price_complaints(shop["reviews"])
        details = {"name": shop["name"], "blocked_price_mismatch": blocked,
                   "recent_transport_errors": errors, "repeated_public_price_complaints": warning,
                   "same_item_success": bool(successes),
                   "past_price_reference_only": successes[-1]["price"] if successes else None}
        evidence.append(details)
        if not blocked:
            # Risk comes before preference for past successful purchases.
            rank = (errors >= 2, warning, not bool(successes), shop["name"])
            candidates.append((rank, shop, details))
    if not candidates:
        return None, "No eligible shop: direct receipt-mismatch evidence for all candidates.", evidence
    _, shop, details = min(candidates, key=lambda entry: entry[0])
    reason = (f"{shop['name']}: repeated price complaints={details['repeated_public_price_complaints']}, "
              f"recent connection errors={details['recent_transport_errors']}, "
              f"same-item past success={details['same_item_success']}. "
              "Past prices are references, not current offers; name breaks ties.")
    return shop, reason, evidence
