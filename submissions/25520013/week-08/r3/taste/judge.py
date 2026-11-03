"""v1.19 pinned taste judge (harness/lock.json pins this file; taste/gate.py refuses to score with another hash).

  r = hour_loss(events, catalog, t, A, B, hour_s=3600)   # {n, counts, seqs, L_A, L_B, d}

Realized = market settles (type "settle", src "market", a shop and a sku) with t <= ts < t + hour_s, category from
the catalog, else the settle's own. L_X = -sum_c n_c log p_X(c), the multinomial log loss without the coefficient
(it cancels in d); a category missing from a forecast gets that forecast's smallest probability.
d = L_B - L_A: d > 0 means the taste forecast A was better. Pure: no I/O, no clock, no randomness.
"""
import math


def realized(events, catalog, t, hour_s=3600):
    counts, seqs = {}, []
    for e in events:
        if e.get("type") != "settle" or e.get("src") != "market":
            continue
        ts, d = e.get("ts"), e.get("data") or {}
        if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not t <= ts < t + hour_s:
            continue
        if not d.get("shop") or not d.get("sku"):
            continue
        cat = ((catalog or {}).get(d["sku"]) or {}).get("category") or d.get("category")
        if not cat:
            continue
        counts[cat] = counts.get(cat, 0) + 1
        if isinstance(e.get("seq"), int) and not isinstance(e.get("seq"), bool):
            seqs.append(e["seq"])
    return counts, sorted(seqs)


def log_loss(counts, p):
    floor = min(p.values())
    return -sum(n * math.log(p.get(c, floor)) for c, n in counts.items())


def hour_loss(events, catalog, t, A, B, hour_s=3600):
    counts, seqs = realized(events, catalog, t, hour_s)
    la, lb = log_loss(counts, A), log_loss(counts, B)
    return {"n": sum(counts.values()), "counts": counts, "seqs": seqs, "L_A": la, "L_B": lb, "d": lb - la}
