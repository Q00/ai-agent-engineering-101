"""Turn 1 for unknown buyers of food listed under 30k with no offer yet: open close to the floor (floor + 25% of the list-to-floor gap) instead of near list price, so fewer buyers walk on the first ask.

Forged 2026-11-11 07:37 by harness.forge for states ['food|<30k|1|none|False']."""

def decide(v):
    if v.get("kind") != "food":
        return None
    L = v.get("list_price") or 0
    if L >= 30000 or v.get("turn") != 1 or v.get("known"):
        return None
    if v.get("offer") is not None:
        return None
    f = v["floor"]
    c = v.get("cost") or 0
    base = max(f, c)
    gap = L - base
    if gap < 0:
        gap = 0
    p = int(base + 0.25 * gap)
    fi = int(f)
    if fi < f:
        fi += 1
    if p < fi:
        p = fi
    return {"action": "counter", "price": p}

