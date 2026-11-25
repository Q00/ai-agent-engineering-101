"""For known buyers of goods listed under 1M who have not offered by turn 2, cut the ask to about 30% of the way from floor to the last ask so the buyer stays; otherwise let the rules play.

Forged 2026-11-10 23:37 by harness.forge for states ['goods|<1M|2|none|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 1000000 or v.get("turn") != 2:
        return None
    floor = int(v.get("floor") or 0)
    cost = int(v.get("cost") or 0)
    low = max(floor, cost)
    offer = v.get("offer")
    if offer is not None:
        if offer >= floor and offer >= cost:
            return {"action": "accept"}
        return None
    ref = v.get("ask")
    if ref is None:
        ref = lp
    ref = int(ref)
    if ref <= low:
        return {"action": "counter", "price": low}
    price = int(low + 0.3 * (ref - low))
    if price >= ref:
        price = ref - 1
    if price < low:
        price = low
    return {"action": "counter", "price": price}
