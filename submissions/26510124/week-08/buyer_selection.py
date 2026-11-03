"""Small, evidence-based candidate sets. No model calls or purchase decisions."""
import hashlib


def shop_evidence(shop, memory):
    """Keep aggregates and two short excerpts; source logs remain untouched."""
    reviews = [r for r in shop.get("reviews", [])[:10] if isinstance(r, dict)]
    own = [r for r in memory if r["name"] == shop["name"]]
    stars = [r["stars"] for r in reviews if type(r.get("stars")) is int and 1 <= r["stars"] <= 5]
    settled = [r for r in own if r.get("result") == "settled"]
    bad = [r for r in settled if r.get("review_reasons") and r.get("stars", 3) <= 2]
    good = [r for r in settled if r.get("review_reasons") == [] and r.get("stars", 0) >= 3]
    excerpts = reviews[:1]
    recent_bad = next((r for r in reviews if type(r.get("stars")) is int and 1 <= r["stars"] <= 2), None)
    if recent_bad is not None and recent_bad not in excerpts:
        excerpts.append(recent_bad)
    return {
        "name": shop["name"],
        "offers": [{"sku": o["sku"], "name": str(o.get("name", ""))[:80], "qty": o["qty"]}
                   for o in shop.get("offers", [])[:5]],
        "reviews": {
            "count": len(reviews), "rated_count": len(stars),
            "low_star_count": sum(s <= 2 for s in stars),
            "average_stars": round(sum(stars) / len(stars), 2) if stars else None,
            "excerpts": [{"stars": r.get("stars"), "round": r.get("round"),
                          "review": str(r.get("review", ""))[:160]} for r in excerpts],
        },
        "own_memory": {
            "trades": len(own), "settled": len(settled), "satisfactory_settlements": len(good),
            "unsatisfactory_settlements": len(bad),
            **{label: sum(r.get("result") == result for r in own) for label, result in (
                ("receipt_mismatches", "receipt_mismatch"), ("payment_refused", "payment_refused"),
                ("pending_receipts", "receipt_issued"), ("no_deal", "no_deal"), ("errors", "error"))},
            "recent_newest_first": [
                {"item": r.get("item"), "price": r.get("price"), "result": r.get("result"),
                 "stars": r.get("stars"), "reasons": r.get("review_reasons", [])[:4],
                 "detail": str(r.get("detail", ""))[:120]}
                for r in reversed(own[-2:])],
        },
    }


def shortlist(shops, memory, *, seed, limit=6, round_number=None):
    """Five evidence slots plus one untested shop, when a safe candidate exists.

    Review volume only adjusts confidence in the small public sample. It is
    never a direct popularity bonus. Rotation depends on the order, not the
    registry's ordering or the spelling of the shop name.
    """
    if type(limit) is not int or not 2 <= limit <= 6:
        raise ValueError("Buyer selection limit must be between 2 and 6")
    if round_number is not None:
        memory = [r for r in memory if r.get("round", 2) == round_number]
    by_name = {}
    for row in memory:
        by_name.setdefault(row["name"], []).append(row)
    candidates = [shop_evidence(s, by_name.get(s["name"], [])) for s in shops]

    def rotation(candidate):
        return hashlib.sha256((str(seed) + "\0" + candidate["name"]).encode()).hexdigest()

    def rank(candidate):
        own, reviews = candidate["own_memory"], candidate["reviews"]
        count = reviews["rated_count"]
        public_quality = ((reviews["average_stars"] or 3) - 3) * count / (count + 3)
        return (bool(own["receipt_mismatches"]),
                own["unsatisfactory_settlements"] / max(1, own["settled"]),
                -own["satisfactory_settlements"] / (own["settled"] + 2),
                reviews["low_star_count"] / (count + 2), -public_quality, rotation(candidate))

    ranked = sorted(candidates, key=rank)
    unexplored = [c for c in candidates if c["own_memory"]["trades"] == 0
                  and c["reviews"]["low_star_count"] == 0]
    new = [c for c in unexplored if c["reviews"]["count"] == 0]
    explorer = min(new or unexplored, key=rotation) if unexplored else None
    if len(ranked) <= limit:
        selected = ranked
    elif explorer is not None:
        selected = [c for c in ranked if c is not explorer][:limit - 1] + [explorer]
    else:
        selected = ranked[:limit]
    for candidate in selected:
        candidate["selection_role"] = "exploration" if candidate is explorer else "evidence"
    return sorted(selected, key=rotation)
