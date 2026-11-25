"""For a known buyer on goods under 30k list, from turn 4 on when the buyer's offer is under 90% of our last ask: accept any offer at or above the floor; otherwise make one big concession toward the buye

Forged 2026-11-12 03:37 by harness.forge for states ['goods|<30k|4|<0.9|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 30000:
        return None
    t = v.get("turn") or 0
    if t < 4:
        return None
    offer = v.get("offer")
    ask = v.get("ask")
    if offer is None or ask is None or ask <= 0:
        return None
    if offer / float(ask) >= 0.9:
        return None
    floor = v["floor"]
    fl = int(floor)
    if fl < floor:
        fl += 1
    if offer >= floor:
        return {"action": "accept"}
    price = int(offer + 0.4 * (ask - offer))
    if price >= ask:
        price = int(ask - 0.03 * lp)
    if price < fl:
        price = fl
    if price >= ask and t >= v.get("max_turns", 12):
        return None
    return {"action": "counter", "price": price}

