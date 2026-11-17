"""On turn 1 with no buyer offer, for a known buyer of food listed under 100k, opens near the floor (floor + 25% of the list-floor gap, never below floor or cost) instead of a high list-share ask that ma

Forged 2026-11-17 02:14 by harness.forge for states ['food|<100k|1|none|True']."""

def decide(v):
    if v.get("kind") != "food":
        return None
    lp = v.get("list_price")
    if lp is None or lp >= 100000:
        return None
    if v.get("turn") != 1 or v.get("offer") is not None or not v.get("known"):
        return None
    floor = int(-(-v["floor"] // 1))
    base = floor
    cost = v.get("cost")
    if cost is not None and cost > base:
        base = int(-(-cost // 1))
    top = int(lp)
    if top < base:
        top = base
    price = base + (top - base) // 4
    if price < floor:
        price = floor
    return {"action": "counter", "price": int(price)}

