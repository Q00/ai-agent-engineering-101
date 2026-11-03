"""Transparent numerical critics; estimates are scoped, never invented facts."""
from __future__ import annotations
from datetime import datetime
import math
import time


def remaining(unit, now=None):
    if not unit.get("expires_at"):
        return math.inf
    return (unit["expires_at"] - (time.time() if now is None else now)) / 3600


def grade(unit, catalog, now=None):
    if unit.get("kind") != "food":
        return unit.get("grade")
    left = remaining(unit, now)
    if left <= 0:
        return "expired"
    shelf = catalog.get(unit["sku"], {}).get("shelf_hours")
    if not shelf:
        return unit.get("grade")
    return "A" if left / shelf >= .8 else "B" if left / shelf >= .45 else "C"


def buyer_value(product, price, *, broken=False, refund=0, hours_left=math.inf, use_hours=0):
    useful = not broken and (product.get("kind") != "food" or hours_left >= use_hours)
    value = product["list_price"] if useful else 0
    return {"value": value, "surplus": value - price + refund}


def expected_surplus(list_price, price, failure=0, covered_failure=0):
    if not 0 <= covered_failure <= failure <= 1:
        raise ValueError("invalid_failure_probabilities")
    return (1 - failure) * list_price - price + price * covered_failure


def shop_statistics(journal, sku, category=None):
    visits = [e for e in journal.events("shop_opportunity", 10000) if e.get("sku") == sku]
    paid = [e for e in journal.events("shop_settled", 10000) if e.get("sku") == sku]
    return {"opportunities": len(visits), "settled": len(paid),
            "settlement_rate": (len(paid) + 1) / (len(visits) + 2),
            "prices": [e["price"] for e in paid[:20]],
            "source": "own observed opportunities, with a disclosed Beta(1,1) rate prior"}


def shop_candidates(unit, inv, catalog, journal, config, *, offer=None, previous_ask=None, now=None):
    now = time.time() if now is None else now
    left = remaining(unit, now)
    if left <= 0:
        return []
    L, cost = unit["list_price"], unit["cost"]
    stats = shop_statistics(journal, unit["sku"])
    # Observed paid prices remain observations, not guaranteed current demand.
    reference = sorted(stats["prices"])[len(stats["prices"]) // 2] if stats["prices"] else .85 * L
    urgency = max(0, min(1, left / max(1, catalog.get(unit["sku"], {}).get("shelf_hours") or 24)))
    if unit.get("kind") == "food":
        recovery_floor = max(1, int(min(cost, .55 * L) * min(1, urgency * 3)))
    else:
        recovery_floor = max(1, int(cost * .95))
    horizon = datetime.fromisoformat(config["end_at"]).timestamp() - now
    if horizon < 6 * 3600:
        recovery_floor = max(1, int(recovery_floor * .65))
    ceiling = previous_ask or int(1.05 * L)
    prices = {max(recovery_floor, int(reference * f)) for f in (.9, 1, 1.1)}
    if previous_ask:
        prices.add(previous_ask)
    result = []
    warranty = 6 if unit.get("kind") == "goods" else 0
    # Unknown break risk is an explicit stress scenario, not an empirical estimate.
    stress_refund = .15 if warranty else 0
    for p in sorted({min(ceiling, p) for p in prices}):
        result.append({"id": "quote:" + str(p), "action": "counter", "price": p,
                       "warranty_hours": warranty, "score": (1 - stress_refund) * p * stats["settlement_rate"],
                       "evidence": stats, "recovery_floor": recovery_floor,
                       "risk": "goods refund stress 0.15; not observed break probability" if warranty else "food expiry"})
    if offer and offer >= recovery_floor:
        p = min(offer, previous_ask) if previous_ask else offer
        result.append({"id": "accept:" + str(p), "action": "accept" if p == offer else "counter", "price": p,
                       "warranty_hours": warranty, "score": (1 - stress_refund) * p,
                       "evidence": stats, "recovery_floor": recovery_floor})
    return result


def buyer_candidates(order, shops, catalog, journal):
    past = journal.events("purchase", 10000)
    failures = journal.events("attempt", 2000)
    result = []
    allowed = set(order.get("acceptable") or [])
    for shop in shops:
        for row in shop.get("offers", []):
            sku = row.get("sku")
            if sku not in allowed or row.get("qty", 0) < 1 or sku not in catalog:
                continue
            product = catalog[sku]
            own = [x for x in past if x.get("shop") == shop["url"] and x.get("sku") == sku]
            failed = [x for x in failures if x.get("shop") == shop["url"] and not x.get("success")
                      and x.get("kind") not in {"model_error", "local_error"}]
            estimate = sorted(x["price"] for x in own)[len(own) // 2] if own else int(.8 * product["list_price"])
            estimate = min(estimate, order["budget"])
            success = (len(own) + 1) / (len(own) + len(failed) + 2)
            result.append({"id": shop["name"] + ":" + sku, "shop": shop["url"], "name": shop["name"],
                           "sku": sku, "product": product, "reference_price": estimate,
                           "score": success * (product["list_price"] - estimate),
                           "evidence": {"own_paid": len(own), "peer_failures": len(failed),
                                        "price_is_prior": not bool(own)}})
    return sorted(result, key=lambda x: x["score"], reverse=True)


def restock_candidates(inv, catalog, demand, journal, config, now=None):
    now = time.time() if now is None else now
    end = datetime.fromisoformat(config["end_at"]).timestamp()
    if end - now < 3600:
        return []
    units = [u for u in inv.get("units", []) if remaining(u, now) > 0]
    count = {}
    for u in units:
        count[u["sku"]] = count.get(u["sku"], 0) + 1
    target = config["inventory_target_units"]
    if len(units) >= target:
        return []
    liability = sum(e.get("price", 0) for e in journal.events("shop_settled", 10000)
                    if now - e.get("paid_at", 0) < e.get("warranty_hours", 0) * 3600)
    spendable = max(0, inv.get("balance", 0) * (1 - config["cash_reserve_fraction"]) - liability)
    signals = {}
    for e in journal.events("public_settle", 20000):
        product = catalog.get(e.get("item") or e.get("sku"), {})
        cat = product.get("category")
        if cat:
            signals[cat] = signals.get(cat, 0) + math.exp(-(now - e["_ts"]) / 21600)
    for e in journal.events("stockout", 2000):
        product = catalog.get(e.get("sku"), {})
        if product:
            signals[product["category"]] = signals.get(product["category"], 0) + 1
    candidates = []
    # A deterministic rotating probe avoids presenting unobserved demand as fact.
    probe = int(now // 3600)
    for sku, p in catalog.items():
        if count.get(sku, 0) >= config["restock_max_qty"] or .75 * p["list_price"] > spendable:
            continue
        if p.get("kind") == "food" and (p.get("shelf_hours") or 0) < 6:
            continue
        cat_signal = signals.get(p["category"], 0)
        import hashlib
        draw = int(hashlib.sha256((str(probe) + sku).encode()).hexdigest()[:8], 16) / 2**32
        scarcity = 1 / (1 + count.get(sku, 0))
        score = scarcity * (cat_signal + config["exploration_fraction"] * draw) / math.sqrt(max(1, p["list_price"]))
        candidates.append({"id": sku, "sku": sku, "qty": 1, "score": score,
                           "max_cost": math.ceil(.75 * p["list_price"]), "category": p["category"],
                           "evidence": {"observed_category_signal": cat_signal, "probe": cat_signal == 0}})
    chosen, covered = [], set()
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        if c["category"] in covered or c["max_cost"] > spendable:
            continue
        chosen.append(c)
        covered.add(c["category"])
        spendable -= c["max_cost"]
        if len(chosen) >= min(config["restock_max_lines"], target - len(units)):
            break
    return chosen
