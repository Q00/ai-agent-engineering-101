"""Bounded local selection shortcuts; every purchase still negotiates/checks anew."""
import hashlib
import time
from datetime import datetime
from fractions import Fraction

from buyer_policy import buying_terms
from buyer_selection import value_ratio


def exploration_turn(seed):
    """Reserve one in five deterministic order buckets for fresh comparisons."""
    return int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16) % 5 == 0


def rotation(seed, name, sku=""):
    return hashlib.sha256((str(seed) + "\0" + name + "\0" + sku).encode()).hexdigest()


def _timestamp(row):
    try:
        stamp = datetime.fromisoformat(row["time"])
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def recent_success_choice(candidates, memory, *, budget, config, order, catalog,
                          seed, now=None):
    """Reuse verified past selection, never its receipt or price authorization.

    Only a recent confirmed same-SKU settlement with an attached fault-free
    review and the same checkout minimums qualifies. Preference-only low stars
    remain usable. New inventory can differ: certificate, price, and warranty
    checks still run on the new transaction after negotiation.
    """
    seconds = config.get("selection_cache_seconds", 900)
    if type(seconds) is not int or not 0 <= seconds <= 3600:
        raise ValueError("Invalid buyer policy: selection_cache_seconds")
    if (seconds == 0 or order.get("round", 0) < 3 or not isinstance(catalog, dict)
            or exploration_turn(seed)):
        return None
    now = time.time() if now is None else now
    latest, uncertain = {}, set()
    for row in memory:
        if row.get("round") != order["round"]:
            continue
        key = (row.get("name"), row.get("item"))
        stamp = _timestamp(row)
        if stamp is None or stamp > now:
            uncertain.add(key)
        elif key not in latest or stamp >= latest[key][0]:
            latest[key] = (stamp, row)
    reusable = []
    for candidate in candidates:
        own = candidate["own_memory"]
        if (own["receipt_mismatches"] or own["unsatisfactory_settlements"]
                or candidate["reviews"]["low_star_count"]):
            continue
        for offer in candidate["offers"]:
            sku = offer["sku"]
            key = (candidate["name"], sku)
            if key in uncertain or key not in latest:
                continue
            stamp, row = latest[key]
            if not now - seconds <= stamp <= now:
                continue
            if (row.get("result") != "settled" or type(row.get("ledger")) is not int
                    or row["ledger"] <= 0 or row.get("verified_review") is not True
                    or row.get("review_issues") != []):
                continue
            price, old = row.get("price"), row.get("purchase_policy")
            receipt = row.get("observed_receipt")
            if (type(price) is not int or price <= 0 or not isinstance(old, dict)
                    or not isinstance(receipt, dict) or receipt.get("item") != sku
                    or receipt.get("price") != price or not isinstance(receipt.get("task"), str)
                    or not receipt["task"] or not isinstance(catalog.get(sku), dict)):
                continue
            terms = buying_terms(budget, product=catalog[sku], order=order, config=config)
            if (old.get("accept") != terms["accept"] or old.get("kind") != terms["kind"]
                    or not terms["accept"].get("cert") or price > terms["price_limit"]):
                continue
            reusable.append((Fraction(price, terms["list_price"]), -stamp,
                             rotation(seed, candidate["name"], sku), candidate["name"], sku,
                             row["ledger"]))
    if not reusable:
        return None
    _, _, _, name, sku, ledger = min(reusable)
    return {"name": name, "sku": sku,
            "reason": f"Recent confirmed same-SKU settlement (ledger {ledger}) met the same "
                      "checkout minimums; negotiate and verify this unit anew."}


def timeout_choice(candidates, *, seed):
    """Rank already-filtered candidates locally after a model timeout only.

    This chooses where to negotiate, not whether/at what price to purchase.
    Review volume is not a popularity bonus. The existing exploration candidate
    remains eligible, including a shop with no reviews or own history.
    """
    if not candidates:
        return None

    def rank(candidate):
        own, reviews = candidate["own_memory"], candidate["reviews"]
        count = reviews["rated_count"]
        public_quality = ((reviews["average_stars"] or 3) - 3) * count / (count + 3)
        value = value_ratio(candidate)
        return (bool(own["receipt_mismatches"]),
                own["unsatisfactory_settlements"] / max(1, own["settled"]),
                value is None, value if value is not None else 0,
                -(own["satisfactory_settlements"] + own["accepted_tradeoffs"]) / (own["settled"] + 2),
                reviews["low_star_count"] / (count + 2), -public_quality,
                rotation(seed, candidate["name"]))

    explorers = [candidate for candidate in candidates if candidate["selection_role"] == "exploration"
                 and candidate["own_memory"]["trades"] == 0
                 and candidate["reviews"]["low_star_count"] == 0]
    pool = explorers if explorers and exploration_turn(seed) else candidates
    selected = min(pool, key=rank)
    choice = {"name": selected["name"],
              "reason": "Selection model timed out; locally compared confirmed settlements, "
                        "contract faults and bounded public reviews; purchase checks remain unchanged."}
    if selected["offers"]:
        choice["sku"] = min(selected["offers"],
                            key=lambda offer: (
                                min((g["price_to_list"] for g in offer.get("price_history", [])), default=float("inf")),
                                rotation(seed, selected["name"], offer["sku"])))["sku"]
    return choice
