"""For known goods buyers at the walk-prone turn (turn 3 under 30k list, or turn 4 at 1M+ list) who offer well below our ask: accepts any offer at or above the floor, otherwise counters with a large step

Forged 2026-11-13 05:06 by harness.forge for states ['goods|<30k|3|<0.9|True', 'goods|1M+|4|<0.97|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v["list_price"]
    t = v["turn"]
    offer = v.get("offer")
    ask = v.get("ask")
    if offer is None or offer <= 0:
        return None
    if ask is None or ask <= 0:
        ask = lp
    r = offer / ask
    if lp < 30000 and t == 3 and r < 0.9:
        gap = 0.5
    elif lp >= 1000000 and t == 4 and r < 0.97:
        gap = 0.35
    else:
        return None
    floor = v["floor"]
    fl = int(floor)
    if fl < floor:
        fl += 1
    if offer >= floor:
        return {"action": "accept"}
    p = int(offer + gap * (ask - offer))
    if p > ask:
        p = int(ask)
    if p < fl:
        p = fl
    return {"action": "counter", "price": p}

