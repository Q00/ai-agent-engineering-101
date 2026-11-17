"""For known buyers of goods listed under 100k at turns 3-4 with a wide offer/ask gap: accepts any offer at or above the floor (on turn 3 only if the offer is at least 80% of our ask), otherwise counters

Forged 2026-11-11 04:36 by harness.forge for states ['goods|<100k|4|<0.97|True', 'goods|<100k|3|<0.9|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v.get("list_price") or 0
    if lp <= 0 or lp >= 100000:
        return None
    t = v.get("turn")
    ask = v.get("ask")
    off = v.get("offer")
    if t not in (3, 4) or not ask or off is None:
        return None
    floor = v["floor"]
    lim = max(floor, v.get("cost") or 0)
    r = off / ask
    if (t == 4 and r >= 0.97) or (t == 3 and r >= 0.9):
        return None
    if off >= lim and (t == 4 or r >= 0.8):
        return {"action": "accept"}
    target = max(off, lim)
    share = 0.6 if t == 4 else 0.5
    if ask <= target:
        p = target
    else:
        p = target + (1 - share) * (ask - target)
    price = int(-(-p // 1))
    fl = int(-(-lim // 1))
    if price < fl:
        price = fl
    if price > ask and ask >= fl:
        price = int(ask)
    return {"action": "counter", "price": price}

