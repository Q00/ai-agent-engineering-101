"""Food under 100k list, turn 1, known buyer with no offer yet: opens near the floor (floor + 20% of the list-floor gap) instead of the high rule opening that makes 85% of these buyers walk; accepts any 

Forged 2026-11-17 01:16 by harness.forge for states ['food|<100k|1|none|True']."""

def decide(v):
    if v.get("kind") != "food":
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    if v.get("turn") != 1 or not v.get("known"):
        return None
    floor = v.get("floor") or 0
    cost = v.get("cost") or 0
    low = max(floor, cost)
    offer = v.get("offer")
    if offer is not None:
        if offer >= floor and offer >= cost:
            return {"action": "accept"}
        return None
    if low >= lp:
        return {"action": "counter", "price": int(low)}
    target = low + 0.2 * (lp - low)
    price = int(-(-target // 1))
    if price < low:
        price = int(-(-low // 1))
    if price > lp:
        price = int(lp)
    if price < floor:
        price = int(-(-floor // 1))
    return {"action": "counter", "price": price}

