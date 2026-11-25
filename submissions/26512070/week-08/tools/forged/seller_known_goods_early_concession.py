"""For known buyers of goods under 100k list on turn 2 with no buyer offer yet, makes one sizable concession (halfway from our last ask to the floor, never below floor or cost) to keep a buyer who is lik

Forged 2026-11-17 09:47 by harness.forge for states ['goods|<100k|2|none|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    if v.get("turn") != 2 or v.get("offer") is not None:
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    floor = v.get("floor")
    if floor is None:
        return None
    cost = v.get("cost") or 0
    low = max(floor, cost)
    low_i = int(-(-low // 1))
    ask = v.get("ask")
    if ask is None:
        ask = lp
    if ask <= low_i:
        return {"action": "counter", "price": low_i}
    price = ask - (ask - low) * 0.5
    price = int(-(-price // 1))
    if price < low_i:
        price = low_i
    if price >= ask:
        price = int(ask) - 1 if int(ask) - 1 >= low_i else low_i
    return {"action": "counter", "price": price}

