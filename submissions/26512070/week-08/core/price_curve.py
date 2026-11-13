"""Our buyers' acceptance curve: how often a talk ends in a deal at each opening share of list, from our own talks
(user 11/13 11:1x: "fit it from our negotiation logs, but deals count - do not move to the optimum alone").

    py -m core.price_curve            # fit from the last DAYS of seller events, write memory/facts/price_curve.json

The ledger shows settled prices only; what a buyer turned down is in our seller's events: the first counter of a talk
(seller.decision, its unit joined to its SKU through core.market_state.our_units) and how the talk ended
(seller.closed, with the price and the unit's cost when it closed). On 11/10-13 (13,199 talks) goods closed 51% of
talks opened at 0.68 of list, 29% at 0.72, 9% at 0.76; food 67% at 0.60, 12% at 0.70.

Per kind and list band, talks are put in OPEN_STEP buckets of the opening share. The deal rate is made non-increasing
in the share (pooled adjacent violators, weighted by talks), the margin and price per deal are shrunk to the group's
mean. The seller (playbook seller.curve) opens at the bucket with the largest
    deal rate x (margin + volume_weight x price)            (per list price, per talk)
among the buckets whose deal rate is at least min_p of the best deal rate - so a little more margin never costs many
deals - and explores the neighbouring buckets in `explore` of its talks, so the curve keeps learning (also below the
shares tried so far).
"""
import json
import random
import sys
import time

from core import atomic, market_state, paths

DAYS = 4
OPEN_STEP = 0.02
MIN_TALKS = 20               # a bucket with fewer talks is not chosen (it can still be explored)
SHRINK = 10                  # deals that weigh the group mean into a bucket's margin and price
BANDS = (30000, 100000, 300000, 1000000)
DEFAULTS = {"curve_volume_weight": 0.5, "curve_min_p": 0.8, "curve_explore": 0.15}


def out_path():
    return paths.memory() / "facts" / "price_curve.json"


def band(lp):
    for b in BANDS:
        if lp < b:
            return f"<{b // 1000}k"
    return f"{BANDS[-1] // 1000}k+"


def talks(now, days=DAYS):
    """[(kind, list price, open share, deal, price/list, (price - cost)/list)] of our talks in the last days."""
    units = market_state.our_units()
    cat = atomic.read_json(paths.root() / ".market" / "catalog.json", default=None) or {}
    cat = cat.get("items", cat) if isinstance(cat, dict) else {c["sku"]: c for c in cat}
    T = {}
    for d in range(days, -1, -1):
        day = time.strftime("%Y%m%d", time.localtime(now - d * 86400))
        for k in ("seller.decision", "seller.closed"):
            f = paths.memory() / "index" / k / f"{day}.jsonl"
            if not f.exists():
                continue
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    t = (e.get("keys") or {}).get("task")
                    if not t or e["ts"] < now - days * 86400:
                        continue
                    x, dd = T.setdefault(t, {}), e.get("data") or {}
                    if k == "seller.decision":
                        if dd.get("action") == "counter" and "open" not in x and dd.get("unit") in units:
                            x.update(open=dd["price"], unit=dd["unit"])
                    else:
                        x.update(out=dd.get("outcome"), price=dd.get("price"), cost=dd.get("cost"))
    rows = []
    for x in T.values():
        if "open" not in x or "out" not in x:
            continue
        c = cat.get(units.get(x["unit"])) or {}
        lp = c.get("list_price")
        if not lp or c.get("kind") not in ("goods", "food"):
            continue
        deal = x["out"] == "completed" and isinstance(x.get("price"), (int, float))
        rows.append((c["kind"], lp, x["open"] / lp, deal, x["price"] / lp if deal else None,
                     (x["price"] - (x.get("cost") or x["price"])) / lp if deal else None))
    return rows


def _pava_down(ps, ws):
    """Weighted least squares fit that does not increase (pooled adjacent violators)."""
    blocks = []
    for p, w in zip(ps, ws):
        blocks.append([p * w, w, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            s, w2, n = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += w2
            blocks[-1][2] += n
    out = []
    for s, w, n in blocks:
        out += [s / w] * n
    return out


def fit_group(rows):
    deals = [r for r in rows if r[3]]
    if not deals:
        return None
    m0 = sum(r[5] for r in deals) / len(deals)
    p0 = sum(r[4] for r in deals) / len(deals)
    b = {}
    for r in rows:
        k = round(round(r[2] / OPEN_STEP) * OPEN_STEP, 2)
        x = b.setdefault(k, {"n": 0, "d": 0, "m": 0.0, "p": 0.0})
        x["n"] += 1
        if r[3]:
            x["d"] += 1
            x["m"] += r[5]
            x["p"] += r[4]
    keys = sorted(b)
    rate = _pava_down([(b[k]["d"] + 0.5) / (b[k]["n"] + 1) for k in keys], [b[k]["n"] for k in keys])
    table = []
    for k, p in zip(keys, rate):
        x = b[k]
        w = x["d"] / (x["d"] + SHRINK)
        table.append({"open": k, "n": x["n"], "deals": x["d"], "p": round(p, 4),
                      "margin": round(w * (x["m"] / x["d"] if x["d"] else m0) + (1 - w) * m0, 4),
                      "price": round(w * (x["p"] / x["d"] if x["d"] else p0) + (1 - w) * p0, 4)})
    return {"talks": len(rows), "deals": len(deals), "table": table}


def fit(now=None):
    now = now or time.time()
    rows = talks(now)
    groups = {}
    for kind in ("goods", "food"):
        mine = [r for r in rows if r[0] == kind]
        g = fit_group(mine)
        if g:
            groups[f"{kind}|all"] = g
        for bd in sorted({band(r[1]) for r in mine}):
            g = fit_group([r for r in mine if band(r[1]) == bd])
            if g and g["talks"] >= 5 * MIN_TALKS:
                groups[f"{kind}|{bd}"] = g
    doc = {"ts": now, "days": DAYS, "talks": len(rows), "groups": groups}
    atomic.write_json(out_path(), doc)
    _CACHE.clear()
    return {"talks": len(rows), "groups": {k: choose(g)[0] for k, g in groups.items()}}


def choose(g, pb=None):
    """(open share, its table row) by the rule in the module doc."""
    pb = {**DEFAULTS, **(pb or {})}
    cand = [r for r in g["table"] if r["n"] >= MIN_TALKS]
    if not cand:
        return None, None
    best_p = max(r["p"] for r in cand)
    ok = [r for r in cand if r["p"] >= pb["curve_min_p"] * best_p]
    r = max(ok, key=lambda r: r["p"] * (r["margin"] + pb["curve_volume_weight"] * r["price"]))
    return r["open"], r


_CACHE = {}


def _doc():
    f = out_path()
    try:
        mt = f.stat().st_mtime
    except OSError:
        return None
    if _CACHE.get("mt") != mt:
        _CACHE.clear()
        _CACHE.update(mt=mt, doc=atomic.read_json(f, default=None))
    return _CACHE.get("doc")


def open_share(kind, lp, pb=None, rng=random):
    """The opening share for a talk on this unit, or None without a fitted curve. `explore` of the talks open one
    bucket lower or higher than the choice."""
    doc = _doc()
    if not doc or not lp:
        return None
    g = doc["groups"].get(f"{kind}|{band(lp)}") or doc["groups"].get(f"{kind}|all")
    if not g:
        return None
    s, _ = choose(g, pb)
    if s is None:
        return None
    pb = {**DEFAULTS, **(pb or {})}
    if rng.random() < pb["curve_explore"]:
        s += rng.choice((-OPEN_STEP, OPEN_STEP))
    return round(min(0.95, max(0.5, s)), 2)


if __name__ == "__main__":
    print(json.dumps(fit(), ensure_ascii=False))
    sys.exit(0)
