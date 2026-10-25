"""Read our seller's locally recorded negotiation results."""
import json
from pathlib import Path
from pricing_state import DATA_DIR, POLICY

HISTORY_PATH = DATA_DIR / "logs" / "seller-history.jsonl"
REVIEWS_PATH = (
    Path(__file__).resolve().parent
    / "logs"
    / "challenge-1"
    / "before-reviews-20261025T083209Z.json"
)

def load_recent_results(item, limit=20):
    if not HISTORY_PATH.exists():
        return []

    results = []
    with HISTORY_PATH.open(encoding="utf-8") as history:
        for line in history:
            if not line.strip():
                continue

            event = json.loads(line)
            if (
                event["item"] == item
                and event["event"] in {"deal", "rejected", "canceled"}
            ):
                results.append(event)

    return results[-limit:]

def load_shop_reviews(limit=10):
    if not REVIEWS_PATH.exists():
        return []

    snapshot = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    reviews = sorted(
        snapshot["data"],
        key=lambda review: review["ts"],
        reverse=True,
    )

    return [
        {
            "author": review["author"],
            "stars": review["stars"],
            "review": review["review"],
            "timestamp": review["ts"],
        }
        for review in reviews[:limit]
    ]

def load_review_window(item, last_reviewed_task=None):
    results = []
    seen_tasks = set()

    if HISTORY_PATH.exists():
        with HISTORY_PATH.open(encoding="utf-8") as history:
            for line in history:
                if not line.strip():
                    continue

                event = json.loads(line)
                if (
                    event["item"] == item
                    and event["event"] in {"deal", "rejected"}
                    and event.get("policy") == POLICY
                    and event["task"] not in seen_tasks
                ):
                    results.append(event)
                    seen_tasks.add(event["task"])

    if last_reviewed_task is not None:
        task_ids = [event["task"] for event in results]
        if last_reviewed_task not in task_ids:
            raise ValueError("Pricing checkpoint is missing from history")

        checkpoint = task_ids.index(last_reviewed_task)
        results = results[checkpoint + 1:]

    if len(results) < 6:
        return []

    return results[-6:]