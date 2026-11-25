"""On turn 1 for unknown buyers of food listed under 100k with no offer yet, opens with a modest ask (floor plus 35% of the list-to-floor gap, never below floor or cost) instead of the high rule opening 

Forged 2026-11-13 13:37 by harness.forge for states ['food|<100k|1|none|False']."""

def decide(v):
    if v.get("kind") != "food":
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    if v.get("turn") != 1 or v.get("known"):
        return None
    floor = int(v.get("floor") or 0)
    cost = int(v.get("cost") or 0)
    offer = v.get("offer")
    if offer is not None:
        if offer >= floor and offer >= cost:
            return {"action": "accept"}
        return None
    base = max(floor, cost)
    if lp <= base:
        return {"action": "counter", "price": int(base)}
    price = int(base + 0.35 * (lp - base))
    if price > lp:
        price = int(lp)
    if price < base:
        price = int(base)
    return {"action": "counter", "price": price}

