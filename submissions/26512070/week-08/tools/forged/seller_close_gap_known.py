"""For known buyers whose offer is far below our ask (food under 30k at turn 4 or later with offer/ask under 0.8; goods under 300k at turn 3 or later with offer/ask under 0.9): accept any offer at or abo

Forged 2026-11-11 03:07 by harness.forge for states ['food|<30k|4|<0.8|True', 'goods|<300k|3|<0.9|True']."""

def decide(v):
    offer = v.get("offer")
    ask = v.get("ask")
    floor = int(v["floor"])
    lp = v.get("list_price") or 0
    t = v.get("turn") or 1
    kind = v.get("kind")
    if not v.get("known"):
        return None
    if offer is None or ask is None or ask <= 0:
        return None
    ratio = offer / ask
    if kind == "food":
        if not (lp < 30000 and t >= 4 and ratio < 0.8):
            return None
        keep = 0.35
    elif kind == "goods":
        if not (lp < 300000 and t >= 3 and ratio < 0.9):
            return None
        keep = 0.4
    else:
        return None
    if offer >= floor:
        return {"action": "accept"}
    gap = ask - offer
    price = int(offer + gap * keep)
    if t >= v.get("max_turns", 12) - 2:
        price = floor
    if price > ask:
        price = ask
    if price < floor:
        price = floor
    return {"action": "counter", "price": int(price)}

