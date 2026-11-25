"""On turn 1 for goods under 1M with no buyer offer and an unknown buyer, open near the floor (floor + 30% of the list-floor gap, never below cost) instead of a high opening ask that makes buyers walk; o

Forged 2026-11-11 09:06 by harness.forge for states ['goods|<1M|1|none|False']."""

def decide(v):
    if v.get("kind") != "goods":
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 1000000:
        return None
    if v.get("turn") != 1 or v.get("offer") is not None or v.get("known"):
        return None
    floor = v.get("floor") or 0
    cost = v.get("cost") or 0
    low = max(floor, cost)
    if lp <= low:
        price = int(low) + (0 if int(low) == low else 1)
        return {"action": "counter", "price": max(price, int(floor) + (0 if int(floor) == floor else 1))}
    price = low + 0.30 * (lp - low)
    price = int(price + 0.999999)
    if price < floor:
        price = int(floor) + (0 if int(floor) == floor else 1)
    if price < cost:
        price = int(cost) + (0 if int(cost) == cost else 1)
    if price > lp:
        price = int(lp)
        if price < floor:
            price = int(floor) + (0 if int(floor) == floor else 1)
    return {"action": "counter", "price": price}

