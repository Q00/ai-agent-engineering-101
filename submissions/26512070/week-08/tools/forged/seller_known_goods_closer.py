"""For known goods buyers: opens at the midpoint between floor and list on turn 1, and from turn 4 accepts any offer at or above the floor, otherwise counters with a large step toward the floor so the bu

Forged 2026-11-11 01:36 by harness.forge for states ['goods|<1M|1|none|True', 'goods|<1M|4|<0.9|True', 'goods|1M+|4|<0.8|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v["list_price"]
    fl = max(v["floor"], v["cost"])
    t = v["turn"]
    offer = v.get("offer")
    ask = v.get("ask")

    def up(x):
        p = int(x)
        if p < x:
            p += 1
        if p < fl:
            p = int(fl)
            if p < fl:
                p += 1
        return p

    if t == 1 and offer is None:
        target = min(0.92 * lp, fl + 0.5 * (lp - fl))
        return {"action": "counter", "price": up(max(fl, target))}
    if t >= 4 and offer is not None:
        if offer >= fl:
            return {"action": "accept"}
        top = ask if ask is not None else lp
        if top < fl:
            top = fl
        mid = (offer + top) / 2.0
        step = fl + 0.35 * (top - fl)
        return {"action": "counter", "price": up(max(fl, min(top, mid, step)))}
    return None
