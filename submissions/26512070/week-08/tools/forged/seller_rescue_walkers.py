"""Closes turns where buyers usually walk: on food under 100k at turn 3 with a known buyer whose offer is below 80% of our ask, it accepts any offer at or above the floor, otherwise counters near the flo

Forged 2026-11-11 18:41 by harness.forge for states ['food|<100k|3|<0.8|True', 'goods|1M+|1|none|False']."""

def decide(v):
    fl = v["floor"]
    lp = v["list_price"]
    ask = v.get("ask")
    off = v.get("offer")
    def up(x):
        p = int(x)
        if p < x:
            p += 1
        return p
    if (v["kind"] == "food" and lp < 100000 and v["turn"] == 3 and v["known"]
            and ask and off is not None and off < 0.8 * ask):
        if off >= fl:
            return {"action": "accept"}
        target = off + 0.3 * max(0, ask - off)
        return {"action": "counter", "price": max(up(fl), up(max(target, fl)))}
    if (v["kind"] == "goods" and lp >= 1000000 and v["turn"] == 1
            and off is None and not v["known"]):
        if lp <= fl:
            return {"action": "counter", "price": up(fl)}
        target = fl + 0.3 * (lp - fl)
        return {"action": "counter", "price": max(up(fl), up(min(target, lp)))}
    return None

