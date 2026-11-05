"""Small, evidence-based candidate sets. No model calls or purchase decisions."""
import hashlib
import json
import time

from buyer_policy import buying_terms
from payment_memory import acceptance_refusal, record_time


def shop_evidence(shop, memory):
    """Keep aggregates and two short excerpts; source logs remain untouched."""
    reviews = [r for r in shop.get("reviews", [])[:10] if isinstance(r, dict)]
    own = [r for r in memory if r["name"] == shop["name"]]
    stars = [r["stars"] for r in reviews if type(r.get("stars")) is int and 1 <= r["stars"] <= 5]
    settled = [r for r in own if r.get("result") == "settled"]
    bad = [r for r in settled if r.get("review_issues", r.get("review_reasons")) and r.get("stars", 3) <= 2]
    good = [r for r in settled if r.get("review_reasons") == [] and r.get("stars", 0) >= 3]
    tradeoffs = [r for r in settled if r.get("review_issues") == [] and r.get("review_preference_shortfalls")]
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
            "accepted_tradeoffs": len(tradeoffs),
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
                -(own["satisfactory_settlements"] + own["accepted_tradeoffs"]) / (own["settled"] + 2),
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


def offer_fingerprint(shop, sku, terms, round_number):
    """Detect observable stock/review/policy changes, never infer hidden grades."""
    offer = next((o for o in shop.get("offers", []) if o.get("sku") == sku), {})
    snapshot = {
        "round": round_number, "sku": sku, "qty": offer.get("qty"), "terms": terms,
        "reviews": [{"stars": r.get("stars"), "round": r.get("round"),
                     "review": str(r.get("review", ""))[:400]}
                    for r in shop.get("reviews", [])[:3] if isinstance(r, dict)],
    }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def compatibility_fingerprint(shop, sku, terms, round_number):
    """Price changes do not cure a known grade or warranty incompatibility.

    Quantity is the stock change visible in the public shop contract. Individual
    units are private, so equal quantity is not proof of equal stock; the short
    cooldown bounds that uncertainty. Reviews and a changing order budget do
    not erase the actual checkout evidence.
    """
    terms = terms if isinstance(terms, dict) else {}
    offer = next((o for o in shop.get("offers", []) if o.get("sku") == sku), {})
    snapshot = {"round": round_number, "sku": sku, "qty": offer.get("qty"),
                "kind": terms.get("kind"), "accept": terms.get("accept", {})}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _confirmed_refusal(record):
    """Only the correlated local payment evidence can provide this marker."""
    marker = record.get("policy_refusal")
    return (record.get("result") == "payment_refused" and isinstance(marker, dict)
            and marker == acceptance_refusal(record.get("detail"), record.get("purchase_policy")))


def _same_acceptance(left, right):
    return (isinstance(left, dict) and isinstance(right, dict)
            and isinstance(left.get("accept"), dict) and left["accept"]
            and left.get("kind") == right.get("kind") and left["accept"] == right.get("accept"))


def reuse_failures(shops, memory, *, item, budget, config, order, catalog=None, now=None):
    """Avoid repeated same-SKU failures without relaxing purchasing conditions.

    Generic no-deal cooldowns may be retried when no alternative exists. Exact
    local ACP grade/warranty refusals stay excluded for the bounded cooldown,
    even then; a new stock quantity, acceptance policy or later same-policy
    settlement permits retry. No model/network error establishes incompatibility.
    """
    now = time.time() if now is None else now
    seconds = config.get("failure_cooldown_seconds", 900)
    if type(seconds) is not int or not 0 <= seconds <= 3600:
        raise ValueError("Buyer failure cooldown must be between 0 and 3600 seconds")
    round_number = order.get("round", 2)
    latest, refusals, successes = {}, {}, {}
    for row in memory:
        if (not isinstance(row, dict) or type(row.get("round", 2)) is not int
                or row.get("round", 2) != round_number or not isinstance(row.get("name"), str)
                or not isinstance(row.get("item"), str)):
            continue
        timestamp = record_time(row.get("time"))
        if timestamp is None or not now - seconds <= timestamp <= now:
            continue
        key = (row["name"], row.get("item"))
        if key not in latest or timestamp >= latest[key][0]:
            latest[key] = (timestamp, row)
        if _confirmed_refusal(row) and (key not in refusals or timestamp >= refusals[key][0]):
            refusals[key] = (timestamp, row)
        if (row.get("result") == "settled" and type(row.get("ledger")) is int and row["ledger"] > 0
                and (key not in successes or timestamp >= successes[key][0])):
            successes[key] = (timestamp, row)
    ready, compatible, skipped, incompatible = [], [], 0, {"grade": 0, "warranty": 0}
    for shop in shops:
        offers = shop.get("offers", [])
        usable, retryable = [], []
        for offer in offers or [{"sku": item}]:
            sku = offer["sku"]
            key = (shop["name"], sku)
            previous, refused, success = latest.get(key), refusals.get(key), successes.get(key)
            product = catalog.get(sku) if catalog is not None else None
            terms = None
            if refused and seconds > 0:
                record = refused[1]
                old_terms = record.get("purchase_policy")
                superseded = success and success[0] >= refused[0] and _same_acceptance(
                    success[1].get("purchase_policy"), old_terms)
                if not superseded:
                    terms = buying_terms(budget, product=product, order=order, config=config)
                    fingerprint = record.get("compatibility_fingerprint")
                    if isinstance(fingerprint, str):
                        unchanged = fingerprint == compatibility_fingerprint(shop, sku, terms, round_number)
                    else:
                        # Older logs lack a dedicated compatibility fingerprint.
                        # Compare their original terms so today's budget alone
                        # cannot erase the failure; changed visible evidence can.
                        unchanged = (_same_acceptance(old_terms, terms)
                                     and isinstance(record.get("selection_fingerprint"), str)
                                     and record["selection_fingerprint"] == offer_fingerprint(
                                         shop, sku, old_terms, round_number))
                    if unchanged:
                        incompatible[record["policy_refusal"]["kind"]] += 1
                        continue
            retryable.append(offer)
            cooling = False
            if (previous and previous[1].get("result") == "no_deal" and seconds > 0
                    and isinstance(previous[1].get("selection_fingerprint"), str)):
                terms = terms or buying_terms(budget, product=product, order=order, config=config)
                cooling = previous[1].get("selection_fingerprint") == offer_fingerprint(shop, sku, terms, round_number)
            if cooling:
                skipped += 1
            else:
                usable.append(offer)
        if usable:
            ready.append({**shop, "offers": usable} if offers else shop)
        if retryable:
            compatible.append({**shop, "offers": retryable} if offers else shop)
    blocked = sum(incompatible.values())
    return ready or compatible, {"cooldown_skipped_offers": skipped if ready else 0,
                                 "all_cooling_reconsidered": bool(compatible and not ready),
                                 "incompatible_skipped_offers": blocked,
                                 "incompatible_grade_offers": incompatible["grade"],
                                 "incompatible_warranty_offers": incompatible["warranty"],
                                 "all_incompatible": bool(blocked and not compatible)}
