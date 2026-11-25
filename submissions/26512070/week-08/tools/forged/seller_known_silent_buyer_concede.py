"""Goods under 100k list, turn 2, known buyer who has not yet offered: makes one large concession to about 40% of the way from the floor to our last ask (at least 3% of list below the ask, never below fl

Forged 2026-11-17 09:19 by harness.forge for states ['goods|<100k|2|none|True']."""

def decide(v):
    if v.get("kind") != "goods" or v.get("turn") != 2:
        return None
    if v.get("offer") is not None or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    floor = v.get("floor")
    if floor is None:
        return None
    cost = v.get("cost") or 0
    lo = int(-(-max(floor, cost) // 1))
    ask = v.get("ask")
    if ask is None:
        ask = lp
    ask = int(ask)
    if ask <= lo:
        return {"action": "counter", "price": lo}
    target = lo + int((ask - lo) * 0.4)
    step = max(1, int(lp * 0.03))
    target = min(target, ask - step)
    price = max(lo, int(target))
    return {"action": "counter", "price": price}

