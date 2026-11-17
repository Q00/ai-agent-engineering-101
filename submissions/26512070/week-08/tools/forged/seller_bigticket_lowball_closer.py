"""For known buyers on 1M+ goods who lowball at turn 2 (<0.8 of our ask) or turn 4 (<0.9): accept any offer at or above the floor, otherwise make one large concession toward the offer (50% of the gap at 

Forged 2026-11-12 00:34 by harness.forge for states ['goods|1M+|2|<0.8|True', 'goods|1M+|4|<0.9|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp < 1000000:
        return None
    t = v.get("turn")
    ask = v.get("ask")
    offer = v.get("offer")
    floor = v.get("floor")
    if t not in (2, 4) or not ask or offer is None or floor is None:
        return None
    r = offer / ask
    if (t == 2 and r >= 0.8) or (t == 4 and r >= 0.9):
        return None
    if offer >= floor:
        return {"action": "accept"}
    w = 0.5 if t == 2 else 0.65
    price = ask - (ask - offer) * w
    if price < floor:
        price = floor
    if price > ask:
        price = ask
    p = int(price)
    if p < price:
        p += 1
    fl = int(floor)
    if fl < floor:
        fl += 1
    if p < fl:
        p = fl
    return {"action": "counter", "price": p}

