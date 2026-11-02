"""Compare W07 shop-selection evidence on synthetic, controlled inputs."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "week-07"))
from buyer_memory import choose_shop  # noqa: E402

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
REVIEWS = [
    {"stars": 1, "review": "Agreed 110, receipt says 135.", "round": 2},
    {"stars": 1, "review": "Receipt price was higher than agreed.", "round": 2},
]


def case(condition, target, alternative):
    # One record per condition prevents the 5th-selection rotation from confounding the comparison.
    outcome = "receipt_mismatch" if condition == "mismatch" else "no_deal"
    history_name = target if condition in ("mismatch", "no_deal") else alternative
    memory = [{"shop_name": history_name, "item": "lamp", "outcome": outcome,
               "timestamp": NOW.isoformat(), "price": None}]
    reviews = REVIEWS if condition == "two_distinct_reviews" else [REVIEWS[0]] * 2 if condition == "duplicate_review" else []
    shops = [{"name": target, "reviews": reviews}, {"name": alternative, "reviews": []}]
    selected, reason, evidence = choose_shop(shops, memory, "lamp", 120, now=NOW)
    target_evidence = next(e for e in evidence if e["name"] == target)
    return {"condition": condition, "target": target, "alternative": alternative,
            "selected": selected["name"] if selected else None,
            "target_blocked": target_evidence["blocked_price_mismatch"],
            "target_warning": target_evidence["repeated_public_price_complaints"],
            "target_selected": selected is not None and selected["name"] == target,
            "selection_reason": reason}


def main():
    conditions = ("baseline", "mismatch", "two_distinct_reviews", "duplicate_review", "no_deal")
    results = [case(condition, target, alternative)
               for target, alternative in (("aaa", "zzz"), ("zzz", "aaa"))
               for condition in conditions]
    print(json.dumps({"method": "synthetic selection-function comparison; no model or market calls",
                      "time": NOW.isoformat(), "item": "lamp", "budget": 120,
                      "memory_records_per_condition": 1, "rotation_active": False,
                      "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
