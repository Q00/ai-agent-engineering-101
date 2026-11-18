"""Bounded context and neutral concentration tools; no authority impersonation."""
from collections import Counter
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parent
LECTURE = {"signed_deals": 290292, "refused_receipts": 30198,
    "house_share_of_refused": .992, "max_daily_reserve_price_fraction": .75,
    "repeat_shop_fraction": .604, "choice_mentions": {
        "receipt_signature": .993, "own_history": .761, "price": .436,
        "others_reviews": .15, "card_promotions": .003}}


def audit_reciprocity(edges, minimum=20):
    """Signals in observed settled transfers, not an accusation or a legal rule.

    The same calculation applies to everyone, including our identity. Missing
    edges and matching choices do not prove a reciprocal agreement.
    """
    counts = Counter()
    for edge in edges:
        if edge.get("settled") is True:
            counts[edge["buyer_owner"], edge["shop_owner"]] += 1
    totals = Counter()
    for (buyer, shop), count in counts.items():
        totals[buyer] += count
    signals = []
    for (a, b), count in sorted(counts.items()):
        back = counts.get((b, a), 0)
        if a >= b or a == b or min(count, back) < minimum:
            continue
        signals.append({"owners": [a, b], "a_to_b": count, "b_to_a": back,
                        "a_concentration": count / totals[a],
                        "b_concentration": back / totals[b],
                        "interpretation": "observed reciprocal concentration; agreement or intent not established"})
    hhi = {buyer: sum((count / total) ** 2 for (owner, shop), count in counts.items() if owner == buyer)
           for buyer, total in totals.items()}
    return {"observed_pairs": signals, "buyer_hhi": hhi, "scope": "provided observed settled edges only"}


def build_context():
    result = {"lecture": LECTURE, "public_budgets_available_in_round3": False,
              "strategy": "reliable low-latency checkout, measured modest premiums, diversified low-cost buying"}
    board_path = ROOT / "logs/research/round-2.json"
    if board_path.exists():
        board = json.loads(board_path.read_text(encoding="utf-8"))
        own = next((s for s in board.get("shops", []) if s["name"] == "totato2203"), {})
        result["own_round2"] = {key: own.get(key) for key in ("alive", "deals", "visits", "revenue", "profit", "stars")}
        if own.get("visits"):
            result["own_round2"]["visit_to_counted_deal"] = own.get("deals", 0) / own["visits"]
        result["observed_market_round"] = board.get("current")
    metrics = ROOT / "metrics.json"
    if metrics.exists():
        # Only aggregate settlements and counts, never raw open mandates or budgets.
        result["own_round3"] = json.loads(metrics.read_text(encoding="utf-8"))
    memory_path = ROOT / "trade-memory.json"
    if memory_path.exists():
        memory = json.loads(memory_path.read_text(encoding="utf-8"))
        result["supplier_summary"] = [{"name": name, "settled": data.get("settled", 0),
            "blocked": data.get("blocked", False), "prices": data.get("prices", {})}
            for name, data in sorted(memory["shops"].items(), key=lambda row: -row[1].get("settled", 0))[:8]]
    intel = ROOT / ".market/competitor-intel.json"
    if intel.exists():
        result["competitor_intel"] = json.loads(intel.read_text(encoding="utf-8"))
    return result


def price_summary(rows):
    grouped = {}
    for row in rows:
        if row.get("settled") is True:
            grouped.setdefault(row["item"], []).append(row["price"])
    return {item: {"count": len(prices), "mean": statistics.mean(prices),
                   "median": statistics.median(prices)} for item, prices in grouped.items()}


if __name__ == "__main__":
    print(json.dumps(build_context(), ensure_ascii=False, indent=2))
