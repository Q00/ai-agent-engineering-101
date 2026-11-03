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


def shop_candidates(unit, inv, catalog, journal, config, *, offer=None, previous_ask=None, now=None,
                    customer_memory=None):
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
    memory_evidence = None
    if customer_memory:
        behavior = customer_memory.get('category_behavior', {})
        personal = customer_memory.get('customer_profile', {})
        source = personal if personal.get('category_paid_visits', 0) >= 3 else behavior
        samples = source.get('category_paid_visits', source.get('paid_visits', 0))
        ratio = source.get('median_paid_to_list_ratio')
        if samples >= 3 and type(ratio) in (int, float) and 0 < ratio <= 2:
            p = int(ratio * L)
            if recovery_floor <= p <= ceiling:
                prices.add(p)
                memory_evidence = {'source': 'verified_customer' if source is personal else 'observed_category',
                                   'paid_samples': samples, 'price_to_list_ratio': ratio}
    if previous_ask:
        prices.add(previous_ask)
    result = []
    warranty = 6 if unit.get("kind") == "goods" else 0
    # Unknown break risk is an explicit stress scenario, not an empirical estimate.
    stress_refund = .15 if warranty else 0
    for p in sorted({min(ceiling, p) for p in prices}):
        from .meta import quote_completion
        completion = quote_completion(journal, unit["sku"], p)
        result.append({"id": "quote:" + str(p), "action": "counter", "price": p,
                       "warranty_hours": warranty, "score": (1 - stress_refund) * p * completion["completion"],
                       "evidence": dict(stats, quote_completion=completion, customer_memory=memory_evidence),
                       "recovery_floor": recovery_floor,
                       "risk": "goods refund stress 0.15; not observed break probability" if warranty else "food expiry"})
    if offer and offer >= recovery_floor:
        p = min(offer, previous_ask) if previous_ask else offer
        result.append({"id": "accept:" + str(p), "action": "accept" if p == offer else "counter", "price": p,
                       "warranty_hours": warranty, "score": (1 - stress_refund) * p,
                       "evidence": stats, "recovery_floor": recovery_floor})
    return result


def buyer_candidates(order, shops, catalog, journal):
    from .forum import shop_hint
    past = journal.events("purchase", 10000)
    failures = journal.events("attempt", 2000)
    result = []
    allowed = set(order.get("acceptable") or [])
    registry = journal.snapshot("registry.json", {})
    for shop in shops:
        for row in shop.get("offers", []):
            sku = row.get("sku")
            if sku not in allowed or row.get("qty", 0) < 1 or sku not in catalog:
                continue
            product = catalog[sku]
            counterpart_key = registry.get(shop["url"], {}).get("pubkey")
            own = [x for x in past if (x.get("shop") == shop["url"] or
                   counterpart_key and x.get("shop_identity") == counterpart_key) and x.get("sku") == sku]
            failed = [x for x in failures if x.get("shop") == shop["url"] and not x.get("success")
                      and x.get("kind") not in {"model_error", "local_error"}]
            estimate = sorted(x["price"] for x in own)[len(own) // 2] if own else int(.8 * product["list_price"])
            estimate = min(estimate, order["budget"])
            success = (len(own) + 1) / (len(own) + len(failed) + 2)
            hint = shop_hint(journal, shop['name'], sku)
            result.append({"id": shop["name"] + ":" + sku, "shop": shop["url"], "name": shop["name"],
                           "sku": sku, "product": product, "reference_price": estimate,
                           "score": success * (product["list_price"] - estimate) * (1.03 if hint else 1),
                           "evidence": {"own_paid": len(own), "peer_failures": len(failed),
                                        "forum_announcement_hint": hint, "forum_hint_is_unverified": True,
                                        "price_is_prior": not bool(own)}})
    return sorted(result, key=lambda x: x["score"], reverse=True)


def supply_open(inv, now=None):
    now = time.time() if now is None else now
    window = inv.get('restock')
    if window is None:
        return True  # Older inventories do not expose a window; the server remains authoritative.
    if not isinstance(window, dict) or window.get('open') is not True:
        return False
    return (type(window.get('opens_at')) in (int, float) and type(window.get('closes_at')) in (int, float)
            and window['opens_at'] <= now < window['closes_at'] - 15)


def cash_capacity(inv, journal, config, now=None):
    now = time.time() if now is None else now
    days = max(0, (datetime.fromisoformat(config['end_at']).timestamp() - now) / 86400)
    liability = sum(e.get('price', 0) for e in journal.events('shop_settled', 10000)
                    if now - e.get('paid_at', 0) < e.get('warranty_hours', 0) * 3600)
    storage = sum(u.get('cost', 0) for u in inv.get('units', []) if u.get('kind') == 'goods') * .01 * days
    balance = inv.get('balance', 0)
    floor = max(config.get('cash_floor', 750000), balance * config['cash_reserve_fraction'])
    return {'spendable': max(0, balance - floor - liability - storage), 'refund_liability': liability,
            'storage_to_end': storage, 'cash_floor': floor, 'remaining_days': days}


def restock_candidates(inv, catalog, demand, journal, config, now=None):
    now = time.time() if now is None else now
    end = datetime.fromisoformat(config["end_at"]).timestamp()
    if end - now < 3600 or not supply_open(inv, now):
        return []
    units = [u for u in inv.get("units", []) if remaining(u, now) > 0]
    count = {}
    for u in units:
        count[u["sku"]] = count.get(u["sku"], 0) + 1
    target = config["inventory_target_units"]
    if len(units) >= target:
        return []
    capacity = cash_capacity(inv, journal, config, now)
    spendable = capacity['spendable']
    signals = {}
    for e in journal.events("public_settle", 20000):
        product = catalog.get(e.get("item") or e.get("sku"), {})
        cat = product.get("category")
        if cat:
            signals[cat] = signals.get(cat, 0) + math.exp(-(now - e["_ts"]) / 21600)
    for e in journal.events("stockout", 2000):
        product = catalog.get(e.get("sku"), {})
        if product and 0 <= now - e['_ts'] < 21600:
            signals[product["category"]] = signals.get(product["category"], 0) + .35 * math.exp(-(now-e['_ts']) / 3600)
    from .forum import signals as forum_signals
    forum = forum_signals(journal, now)
    for category, value in forum['categories'].items():
        signals[category] = signals.get(category, 0) + min(value, 3)
    own = {}
    for event in journal.events('shop_settled', 10000):
        if 0 <= now - event.get('paid_at', event['_ts']) < 3600:
            own[event.get('sku')] = own.get(event.get('sku'), 0) + 1
    candidates = []
    # A deterministic rotating probe avoids presenting unobserved demand as fact.
    probe = int(now // 3600)
    for sku, p in catalog.items():
        fee = .01 * capacity['remaining_days'] if p.get('kind') == 'goods' else 0
        per_unit = math.ceil(.75 * p['list_price'])
        commitment = per_unit * (1 + fee)
        if count.get(sku, 0) >= config["restock_max_qty"] or commitment > spendable:
            continue
        if p.get("kind") == "food" and (p.get("shelf_hours") or 0) < 6:
            continue
        cat_signal = signals.get(p["category"], 0)
        import hashlib
        draw = int(hashlib.sha256((str(probe) + sku).encode()).hexdigest()[:8], 16) / 2**32
        scarcity = 1 / (1 + count.get(sku, 0))
        weight = config.get('category_weights', {}).get(p['category'], 1)
        exact = min(forum['skus'].get(sku, 0), 2)
        # Cover observed own sales through the next supply window, in small lots.
        desired = max(1, math.ceil(own.get(sku, 0) * config.get('supply_cycle_seconds', 1200) / 3600 * 1.5))
        qty = min(20, config['restock_max_qty'] - count.get(sku, 0),
                  max(1, desired - count.get(sku, 0)), max(0, math.floor(spendable / commitment)))
        if qty <= 0:
            continue
        # Capital yield uses disclosed grade probabilities and upper expected cost bounds,
        # not an assumed uniform distribution within supplier price intervals.
        cost_ratio = .60 if p.get('kind') == 'food' else .69
        recovery = .85 * (1 if p.get('kind') == 'food' else .85)
        capital_yield = max(.01, recovery - cost_ratio * (1 + fee)) / .75
        score = weight * scarcity * (cat_signal + exact + 2 * own.get(sku, 0) + config["exploration_fraction"] * draw) * capital_yield
        candidates.append({"id": sku, "sku": sku, "qty": qty, "score": score,
                           "max_cost": per_unit * qty, "capital_commitment": commitment * qty, "category": p["category"],
                           "evidence": {"observed_category_signal": cat_signal, "probe": cat_signal == 0,
                                        "forum_request_signal": exact, 'own_sales_last_hour': own.get(sku, 0),
                                        'cash_capacity': capacity, 'capital_yield_stress': capital_yield,
                                        "adaptive_category_weight": weight}})
    chosen, covered = [], set()
    for c in sorted(candidates, key=lambda x: x["score"], reverse=True):
        remaining_slots = target - len(units) - sum(x['qty'] for x in chosen)
        if remaining_slots <= 0:
            break
        if c['qty'] > remaining_slots:
            fraction = remaining_slots / c['qty']
            c = dict(c, qty=remaining_slots, max_cost=c['max_cost'] * fraction,
                     capital_commitment=c['capital_commitment'] * fraction)
        if c["category"] in covered or c["capital_commitment"] > spendable:
            continue
        chosen.append(c)
        covered.add(c["category"])
        spendable -= c["capital_commitment"]
        if len(chosen) >= min(config["restock_max_lines"], target - len(units)):
            break
    return chosen
