"""For a known buyer on cheap food (<100k list) at turn 1 with no offer yet, open close to the floor (floor + 30% of the list-floor gap) instead of the high rule opening, to stop the 85% walkaways; other

Forged 2026-11-17 01:44 by harness.forge for states ['food|<100k|1|none|True']."""

def decide(v):
    if v.get("kind") != "food":
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    if v.get("turn") != 1 or not v.get("known"):
        return None
    if v.get("offer") is not None:
        return None
    floor = int(v.get("floor") or 0)
    cost = int(v.get("cost") or 0)
    base = max(floor, cost + 1)
    if base > lp:
        return {"action": "counter", "price": max(floor, int(lp))} if lp >= floor else None
    gap = lp - base
    price = int(base + 0.3 * gap)
    if price < base:
        price = base
    if price > lp:
        price = int(lp)
    if price < floor:
        price = floor
    return {"action": "counter", "price": price}

