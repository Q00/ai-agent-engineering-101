"""At turn 4 with a known buyer whose offer sits far below our ask (goods <0.8, food <0.9), accept any offer at or above the floor and cost; otherwise counter well below our ask (35% of the way from offe

Forged 2026-11-10 22:07 by harness.forge for states ['goods|<1M|4|<0.8|True', 'food|<100k|4|<0.9|True', 'goods|<30k|4|<0.8|True', 'food|<100k|4|<0.8|True']."""

def decide(v):
    offer = v.get("offer")
    ask = v.get("ask")
    if not v.get("known") or offer is None or not ask or v.get("turn") != 4:
        return None
    kind = v.get("kind")
    lp = v.get("list_price") or 0
    r = offer / ask
    if kind == "goods":
        if lp >= 1000000 or r >= 0.8:
            return None
    elif kind == "food":
        if lp >= 100000 or r >= 0.9:
            return None
    else:
        return None
    floor = max(v["floor"], v.get("cost") or 0)
    if offer >= floor:
        return {"action": "accept"}
    target = offer + 0.35 * (ask - offer)
    p = max(floor, min(ask, target))
    ip = int(p)
    if ip < p:
        ip += 1
    return {"action": "counter", "price": ip}

