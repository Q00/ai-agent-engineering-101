"""Read our seller's locally recorded negotiation results."""
import json
from pathlib import Path

HISTORY_PATH = (
    Path(__file__).resolve().parent / "logs" / "seller-history.jsonl"
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