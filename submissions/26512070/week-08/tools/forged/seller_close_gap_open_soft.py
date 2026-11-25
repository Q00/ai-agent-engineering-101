"""For known goods buyers at turn 2 whose offer is under 90% of our ask, it concedes 65% of the gap or accepts if the gap is small. For unknown goods buyers at turn 1 on items listed under 30k, it opens 

Forged 2026-11-13 18:06 by harness.forge for states ['goods|<300k|2|<0.9|True', 'goods|<30k|1|none|False']."""

def decide(v):
    if v.get("kind") != "goods":
        return None
    lp = v.get("list_price") or 0
    floor = v.get("floor") or 0
    cost = v.get("cost") or 0
    lo = max(floor, cost)
    turn = v.get("turn")
    ask = v.get("ask")
    offer = v.get("offer")
    known = bool(v.get("known"))
    if lp <= 0:
        return None
    # State 1: known buyer, turn 2, offer far below our ask
    if lp < 300000 and turn == 2 and known and ask and offer is not None and ask > 0 and offer / ask < 0.9:
        target = int(offer + 0.35 * (ask - offer))
        price = max(lo, target)
        if price > ask:
            price = max(lo, int(ask))
        if offer >= lo and (price <= offer or price - offer <= 0.04 * lp):
            return {"action": "accept"}
        return {"action": "counter", "price": int(price)}
    # State 2: unknown buyer, cheap item, opening move with no offer yet
    if lp < 30000 and turn == 1 and offer is None and not known:
        price = max(lo, int(round(0.9 * lp)))
        return {"action": "counter", "price": int(price)}
    return None

