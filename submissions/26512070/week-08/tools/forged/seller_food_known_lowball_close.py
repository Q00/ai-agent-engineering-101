"""For known buyers of cheap food (list <30k) from turn 3 when their offer is under 80% of our last ask: accept any offer at or above the floor, otherwise jump toward the offer (30% of the gap, 15% near 

Forged 2026-11-11 11:07 by harness.forge for states ['food|<30k|3|<0.8|True']."""

def decide(v):
    if v.get("kind") != "food" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 30000 or (v.get("turn") or 0) < 3:
        return None
    ask = v.get("ask")
    offer = v.get("offer")
    if ask is None or offer is None or ask <= 0:
        return None
    if offer >= 0.8 * ask:
        return None
    floor = int(v["floor"])
    if offer >= floor:
        return {"action": "accept"}
    turns_left = (v.get("max_turns") or 12) - v["turn"]
    w = 0.3 if turns_left > 3 else 0.15
    price = int(offer + w * (ask - offer))
    price = min(price, int(ask - 0.04 * lp))
    if price < floor:
        price = floor
    return {"action": "counter", "price": price}

