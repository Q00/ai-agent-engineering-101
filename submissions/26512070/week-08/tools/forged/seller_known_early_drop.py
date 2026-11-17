"""For a known buyer on low-priced goods (list under 300k) who has made no offer by turn 2, counters with one big concession to near the floor (35% of the way from floor to our last ask) instead of the 1

Forged 2026-11-14 13:42 by harness.forge for states ['goods|<300k|2|none|True']."""

def decide(v):
    if v.get("kind") != "goods":
        return None
    lp = v.get("list_price") or 0
    if lp >= 300000 or v.get("turn") != 2:
        return None
    if v.get("offer") is not None or not v.get("known"):
        return None
    floor = v["floor"]
    cost = v.get("cost") or 0
    low = max(floor, cost)
    ask = v.get("ask")
    top = ask if ask is not None else lp
    if top is None or top <= low:
        top = low
    target = low + 0.35 * (top - low)
    p = int(target)
    lo_int = int(-(-low // 1))
    if p < lo_int:
        p = lo_int
    if ask is not None and p >= ask and ask > lo_int:
        p = max(lo_int, int(ask) - 1)
    return {"action": "counter", "price": p}
