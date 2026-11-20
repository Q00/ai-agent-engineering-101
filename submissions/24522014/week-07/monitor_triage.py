"""Prepare small, bounded analysis candidates without invoking a model."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "logs" / "shop-triage"
MAX_CANDIDATES_PER_UTC_DAY = 2
MAX_REVIEW_EXAMPLES = 3
MAX_REVIEW_CHARS = 160


def prepare(record, source_name, output=OUTPUT):
    reasons = []
    if record.get("status") != "ok":
        reasons.append("observation_failed")
    elif record.get("alive") is False:
        reasons.append("shop_offline")
    if record.get("status") == "ok" and record.get("comparison_file"):
        if record.get("deals_change") == 0 and record.get("visits_change", 0) > 0:
            reasons.append("visits_without_sales")
        if (record.get("reviews_change", 0) >= 5 and any(
                "Displayed average rating fell" in a for a in record.get("alerts", []))):
            reasons.append("rating_decline")
    if not reasons:
        return None
    # One candidate per observation; repeated processing preserves the first output.
    key = hashlib.sha256(source_name.encode("utf-8")).hexdigest()[:20]
    observed = datetime.fromisoformat(record["observed_at"]).astimezone(timezone.utc)
    day = observed.strftime("%Y%m%d")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{day}-{key}.json"
    if path.exists():
        return path
    if len(list(output.glob(f"{day}-*.json"))) >= MAX_CANDIDATES_PER_UTC_DAY:
        return None
    examples = [{"stars": r.get("stars"), "review": str(r.get("review", ""))[:MAX_REVIEW_CHARS]}
                for r in record.get("recent_reviews", [])
                if isinstance(r.get("stars"), (int, float)) and r["stars"] < 5][:MAX_REVIEW_EXAMPLES]
    payload = {"source": source_name, "observed_at": record["observed_at"],
               "reasons": reasons, "round": record.get("round"), "alive": record.get("alive"),
               "deals_change": record.get("deals_change"), "visits_change": record.get("visits_change"),
               "stars": record.get("stars"), "reviews_change": record.get("reviews_change"),
               "recent_review_examples": examples,
               "note": "Recent review examples may predate the comparison period. No model was called."}
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path
