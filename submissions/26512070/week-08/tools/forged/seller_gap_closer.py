"""For known buyers on turns 2-4 whose offer is well below our last ask (ratio < 0.9; food <30k only on turn 4), accepts any offer at or above the floor, otherwise counters near the buyer, 30-40% of the 

Forged 2026-11-10 20:37 by harness.forge for states ['food|<30k|4|<0.9|True', 'goods|<300k|4|<0.8|True', 'goods|<300k|4|<0.9|True', 'goods|<1M|2|<0.8|True', 'goods|<300k|2|<0.8|True', 'goods|<300k|3|<0.8|True', 'goods|<1M|3|<0.8|True', 'goods|<100k|4|<0.8|True']."""

def decide(v):
    offer = v.get("offer")
    ask = v.get("ask")
    floor = v["floor"]
    lp = v["list_price"]
    t = v["turn"]
    if not v.get("known") or offer is None or ask is None or ask <= 0:
        return None
    if t < 2 or t > 4 or lp >= 1000000:
        return None
    ratio = offer / ask
    if v["kind"] == "food":
        if lp >= 30000 or t != 4 or ratio >= 0.9:
            return None
    else:
        limit = 0.9 if (t == 4 and lp < 300000) else 0.8
        if ratio >= limit:
            return None
    if offer >= floor:
        return {"action": "accept"}
    k = 0.3 if t == 4 else 0.4
    p = offer + (ask - offer) * k
    price = int(p)
    if price < p:
        price += 1
    if price >= ask:
        price = ask - 1
    if price < floor:
        price = floor
    return {"action": "counter", "price": int(price)}

