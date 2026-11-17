"""Goods under 1M at turn 4 with a known buyer whose offer is under 97% of our ask: accept an offer at or above the floor that is within 8% of our ask, otherwise make a big concession to 40% of the gap a

Forged 2026-11-11 06:07 by harness.forge for states ['goods|<1M|4|<0.97|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp >= 1000000 or v.get("turn") != 4:
        return None
    ask = v.get("ask")
    offer = v.get("offer")
    if ask is None or offer is None or ask <= 0:
        return None
    if offer / ask >= 0.97:
        return None
    floor = v["floor"]
    if offer >= floor and offer >= 0.92 * ask:
        return {"action": "accept"}
    target = offer + 0.4 * (ask - offer)
    p = int(target)
    if p < floor:
        p = int(floor)
        if p < floor:
            p += 1
    if p >= ask and offer >= floor:
        return {"action": "accept"}
    return {"action": "counter", "price": p}

