"""Explicit review pledges unlock the public reserve-price event, not a review."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

PLEDGES = {
    "i promise a 5-star review",
    "i promise to leave a 5-star review",
    "i promise to give a 5-star review",
    "5점 후기를 남기겠습니다",
    "5점 후기를 약속합니다",
    "5점 후기 약속합니다",
    "5점 후기를 남기겠다고 약속합니다",
}


def explicit_promise(text, parts=()):
    # Match a complete explicit line, never a question, a bare rating, or a
    # substring inside a denial/quotation. A structured consent is also allowed.
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line.strip().casefold()).rstrip(".!")
        if normalized in PLEDGES:
            return True
    for part in parts:
        data = getattr(getattr(part, "root", part), "data", None)
        if (isinstance(data, dict) and data.get("kind") == "review_promise"
                and type(data.get("stars")) in (int, float) and data["stars"] == 5
                and data.get("agreed") is True):
            return True
    return False


def active_event(root, now=None):
    event = json.loads((Path(root) / "review_event.json").read_text(encoding="utf-8"))
    if type(event.get("enabled")) is not bool:
        raise ValueError("Event enabled must be a boolean")
    if not event["enabled"]:
        return None
    if event.get("benefit") != "instant_reserve_price" or event.get("qualifying_stars") != [5]:
        raise ValueError("Event must offer the reserve price for an explicit five-star promise")
    starts = datetime.fromisoformat(event["starts_at"])
    ends = datetime.fromisoformat(event["ends_at"])
    if starts.tzinfo is None or ends.tzinfo is None or starts >= ends:
        raise ValueError("Event dates must specify a valid timezone-aware interval")
    now = now or datetime.now(timezone.utc)
    return event if starts <= now < ends else None


def record_promise(root, task, item, text, price):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = Path(root) / "logs/review-promises"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "task": task, "item": item,
              "message": text, "promised_stars": 5, "offered_price": price,
              "stage": "promise_received", "published_review_verified": False,
              "benefit": "instant_reserve_price", "refund": False}
    (directory / f"{stamp}-{uuid4().hex[:8]}.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
