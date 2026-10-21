"""Reproduce this read-only diagnosis from the preserved official snapshots."""
from collections import Counter
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent

def load(name):
    return json.loads((ROOT / name).read_text())

comparison = load("board-comparison.json")
reviews = load("reviews-confirmed.json")
reviews = reviews if isinstance(reviews, list) else reviews["reviews"]
shops = load("shops-confirmed.json")["shops"]
own = next(s for s in shops if s["name"] == "myeonggi")
recent = own["reviews"]
visible_matches = [(r["stars"], r["review"]) for r in recent] == [(r["stars"], r["review"]) for r in reviews[:len(recent)]]
assert visible_matches
runtime = load("seller-runtime-analysis.json")["summary"]
peer_prices = load("peer-verified-prices.json")
summary = {
    "board_captured_at": comparison["captured_at"],
    "board_rows": comparison["rows"],
    "kit_visible_reviews": len(recent),
    "kit_visible_stars": [r["stars"] for r in recent],
    "kit_visible_same_author_count": sum(r["author"] == "cheapest-in-town" for r in reviews[:len(recent)]),
    "kit_visible_mean": sum(r["stars"] for r in recent) / len(recent),
    "latest40_same_author_count": sum(r["author"] == "cheapest-in-town" for r in reviews),
    "latest40_count": len(reviews),
    "smoothed_review_base_using_our_buyer_formula": (sum(r["stars"] for r in recent) + 8) / (len(recent) + 2),
    "restarts": runtime["restarts"],
    "restart_to_registration_minutes": runtime["restart_to_registration_total_seconds"] / 60,
    "observed_reply_latency_max_seconds": runtime["reply_latency_max"],
    "runtime_sample_includes_operator_probes": True,
    "peer_verified_price_sample": peer_prices,
    "limits": [
        "Board visits are market counters, not a measured count of unique people or HTTP page views.",
        "Other buyers' private selection logs are unavailable; loss contributions and causal effects cannot be estimated.",
        "Restart-to-registration intervals do not continuously measure complete external unavailability.",
        "Review concentration is observed; the reasons for the buyer's rating and actual product quality are not independently verified.",
        "The shop description changes are too recent for an isolated effect estimate, and weak traffic predates them.",
    ],
}
assert all(row["deals"] <= row["visits"] for row in comparison["rows"])
assert len(reviews) == 40 and len(recent) == 10
(ROOT / "diagnosis-calculations.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False, indent=2))
