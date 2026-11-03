"""Fixed judge: score(events, shop_names, t_from, t_to) -> {primary, revenue, cost, storage, settles, restocks, seqs}.

Pure function over ledger rows (ledger.py schema {seq, ts, type, src, key, data, role, mine}); reads no files, no
clock. Window t_from <= ts < t_to (None = open). primary = revenue - cost - storage, where
  revenue = sum of settle data.amount whose data.shop is one of shop_names (case-insensitive), buyer-role rows skipped
  cost    = sum of restock data.unit_cost (one restock row = one unit we bought)
  storage = sum of storage data.amount for one of shop_names
Pinned by sha256 in harness/lock.json: an edit to this file makes harness.lock.verify() raise JudgeTampered.
"""

import math


def _num(x):
    return (
        float(x)
        if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
        else 0.0
    )


def score(events, shop_names=(), t_from=None, t_to=None):
    names = {str(n).lower() for n in shop_names}
    rev = cost = sto = 0.0
    settles = restocks = 0
    seqs = []
    for e in events:
        ts, typ, d = e.get("ts"), e.get("type"), e.get("data") or {}
        if (
            not isinstance(ts, (int, float))
            or (t_from is not None and ts < t_from)
            or (t_to is not None and ts >= t_to)
        ):
            continue
        shop = str(d.get("shop") or "").lower()
        if typ == "settle" and shop in names and e.get("role") != "buyer":
            rev += _num(d.get("amount"))
            settles += 1
        elif typ == "restock":
            cost += _num(d.get("unit_cost"))
            restocks += 1
        elif typ == "storage" and shop in names:
            sto += _num(d.get("amount"))
        else:
            continue
        if isinstance(e.get("seq"), int):
            seqs.append(e["seq"])
    return {
        "primary": rev - cost - sto,
        "revenue": rev,
        "cost": cost,
        "storage": sto,
        "settles": settles,
        "restocks": restocks,
        "seqs": seqs,
    }
