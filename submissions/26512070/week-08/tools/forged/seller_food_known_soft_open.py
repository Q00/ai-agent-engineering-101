"""For known buyers of food under 300k on turn 1 with no buyer offer yet: opens at floor + 35% of the list-floor gap instead of a high share of list, to stop 86% first-turn walkaways. Accepts an opening 

Forged 2026-11-16 09:42 by harness.forge for states ['food|<300k|1|none|True']."""

def decide(v):
    if v.get("kind") != "food" or v.get("list_price", 0) >= 300000:
        return None
    if v.get("turn") != 1 or not v.get("known"):
        return None
    lp = int(v["list_price"])
    fl = int(v["floor"])
    cost = int(v.get("cost") or 0)
    off = v.get("offer")
    if off is not None:
        if off >= fl:
            return {"action": "accept"}
        return None
    base = max(fl, cost)
    if lp <= base:
        return {"action": "counter", "price": base}
    p = int(round(base + 0.35 * (lp - base)))
    p = min(p, lp)
    p = max(p, base, fl)
    return {"action": "counter", "price": p}

