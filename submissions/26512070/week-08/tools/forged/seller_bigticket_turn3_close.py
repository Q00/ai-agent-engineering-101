"""For known buyers of 1M+ goods at turn 3 whose offer is under 90% of our last ask: accept any offer at or above the floor that is within 80% of our ask; otherwise counter well toward the buyer (35% of 

Forged 2026-11-13 21:37 by harness.forge for states ['goods|1M+|3|<0.9|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp < 1000000 or v.get("turn") != 3:
        return None
    ask = v.get("ask")
    offer = v.get("offer")
    if ask is None or offer is None or ask <= 0:
        return None
    if offer / ask >= 0.9:
        return None
    floor = v["floor"]
    fl = int(-(-floor // 1))
    if offer >= floor and offer >= 0.8 * ask:
        return {"action": "accept"}
    gap = ask - offer
    target = int(round(offer + 0.35 * gap))
    if target < fl:
        target = fl
    if target > ask:
        target = int(ask) if ask >= fl else fl
    if offer >= floor and target <= offer:
        return {"action": "accept"}
    return {"action": "counter", "price": target}

