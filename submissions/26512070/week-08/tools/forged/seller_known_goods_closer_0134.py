"""For known goods buyers: opens near the floor on turns 1-2 when there is no offer. When a low offer arrives on turns 2-6, it accepts any offer at or above the floor and cost. Otherwise it counters with

Forged 2026-11-13 01:34 by harness.forge for states ['goods|1M+|1|none|True', 'goods|<300k|1|none|True', 'goods|<30k|4|<0.97|True', 'goods|<100k|4|<0.9|True', 'goods|<100k|2|<0.8|True', 'goods|1M+|2|none|True', 'goods|<100k|3|<0.8|True']."""

def decide(v):
    if v.get("kind") != "goods" or not v.get("known"):
        return None
    lp = v["list_price"]
    fl = v["floor"]
    cost = v.get("cost") or 0
    lo = max(fl, cost)
    lo_i = int(lo) if int(lo) >= lo else int(lo) + 1
    if lo_i > lp:
        return None
    ask = v.get("ask")
    offer = v.get("offer")
    turn = v.get("turn") or 1
    mt = v.get("max_turns") or 12
    if offer is not None and offer >= fl and offer >= cost:
        return {"action": "accept"}
    if offer is None:
        if turn > 2:
            return None
        share = 0.4 if turn == 1 else 0.25
        p = lo + (lp - lo) * share
    else:
        if not ask or ask <= 0 or offer / ask >= 0.97 or turn < 2 or turn > 6:
            return None
        gap = ask - offer
        k = 0.5 if turn == 2 else (0.35 if turn == 3 else 0.2)
        p = offer + gap * k
        p = min(p, ask - (ask - lo) * 0.4)
        if turn >= mt - 1:
            p = lo
    if ask:
        p = min(p, ask)
    p = max(int(round(p)), lo_i)
    return {"action": "counter", "price": p}
