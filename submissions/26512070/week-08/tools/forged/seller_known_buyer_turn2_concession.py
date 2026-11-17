"""Goods under 100k list, turn 2, known buyer and no buyer offer yet: make one larger concession, 40% of the gap from our last ask to the floor (or 90% of list if we have no ask), never below the floor o

Forged 2026-11-17 10:15 by harness.forge for states ['goods|<100k|2|none|True']."""

def decide(v):
    if v.get("kind") != "goods":
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    if v.get("turn") != 2 or v.get("offer") is not None or not v.get("known"):
        return None
    floor = v.get("floor")
    if floor is None:
        return None
    cost = v.get("cost") or 0
    lo = max(floor, cost)
    lo = int(-(-lo // 1))
    if lo < floor:
        lo = int(-(-floor // 1))
    ask = v.get("ask")
    if ask is None:
        target = lp * 0.9
    else:
        gap = ask - lo
        if gap <= 0:
            return {"action": "counter", "price": lo}
        target = ask - 0.4 * gap
    target = min(target, lp)
    price = int(round(target))
    if price < lo:
        price = lo
    if ask is not None and price >= ask and ask > lo:
        price = max(lo, int(ask) - 1)
    return {"action": "counter", "price": price}

