"""For known goods buyers who walk under the stock rules: opens <100k items at 88% of list on turn 1 instead of a high share. On turn 4 (<300k) and turn 3 (<1M), when the buyer's offer trails our ask, it

Forged 2026-11-13 10:35 by harness.forge for states ['goods|<100k|1|none|True', 'goods|<300k|4|<0.97|True', 'goods|<1M|3|<0.9|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v["list_price"]
    fl = v["floor"]
    f = int(fl)
    if f < fl:
        f += 1
    t = v.get("turn")
    ask = v.get("ask")
    off = v.get("offer")
    if lp < 100000:
        band = "<100k"
    elif lp < 300000:
        band = "<300k"
    elif lp < 1000000:
        band = "<1M"
    else:
        band = "big"
    if t == 1 and off is None and band == "<100k":
        p = int(round(lp * 0.88))
        return {"action": "counter", "price": max(p, f)}
    if off is None or not ask:
        return None
    r = off / ask
    if band == "<300k" and t == 4 and r < 0.97:
        w = 0.4
    elif band == "<1M" and t == 3 and r < 0.9:
        w = 0.45
    else:
        return None
    if off >= fl:
        return {"action": "accept"}
    p = int(off + w * (ask - off))
    return {"action": "counter", "price": max(p, f)}

