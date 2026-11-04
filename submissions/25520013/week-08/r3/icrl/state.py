"""icrl state S (DESIGN.md sec 3): a compact rolling store built incrementally from events.jsonl (byte offset kept
on disk), market price stats, our shop and buyer summaries, block metrics, inventory valuation and time / gamma.

Our sales = market settles whose unit id is in our restock rows (unit_cost from that row). Balance = the last kit
log '[supply] ... balance B' line (the line has no date, so its time is the read time). Our buyer = settle/deal rows.
Our shop's reviews = review rows with src market, role shop and mine true (on the 2026-11-04 ledger exactly the rows
whose data.shop is our URL); our posts = forum rows whose author is one of our names (owner login / buyer names) with
role buyer.
"""

import json
import math
import os
import re
from pathlib import Path

RETAIN_H = 32
ROUND_END = 1794927540  # 2026-11-17 23:59 KST
SUPPLY_STOP = ROUND_END - 86400  # 2026-11-16 23:59 KST
FREEZE_S = 3 * 3600
SUPPLY_BAN_S = 24 * 3600
GAMMA_DT_S, GAMMA_FLOOR = (
    3600,
    0.0,
)  # = rlpo_v121 config PERIOD_S / GAMMA_FLOOR (test pins them)
STORAGE_PER_DAY = 0.01  # goods: 1 % of cost per day
SUPPLY_RX = re.compile(r"\[supply\] (\d+) x (.+) for (\d+), balance (-?\d+)\s*$")


def gamma(t_rem):
    if t_rem is None or t_rem <= 0:
        return GAMMA_FLOOR
    return max(GAMMA_FLOOR, 1.0 - GAMMA_DT_S / t_rem)


def gamma_at(t):
    return gamma(ROUND_END - t)


def paths(work=None):
    w = Path(work or os.getcwd())
    st = w / os.environ.get("R3_STATE", ".r3")
    md = w / os.environ.get("MARKET_DIR", ".market")
    return {
        "events": st / "events.jsonl",
        "own": st / "own_shops.json",
        "graph": st / "memory" / "graph.json",
        "kit": w / "logs" / "market-kit.log",
        "catalog": md / "catalog.json",
        "demand": md / "demand.json",
    }


def _jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def catalog(P):
    c = _jload(P["catalog"], {}) or {}
    rows = c.values() if isinstance(c, dict) else c
    return {
        x["sku"]: (x.get("category"), x.get("kind"), x.get("list_price"))
        for x in rows
        if x.get("sku")
    }


def _read_new(path, off):
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], off, False
    reset = size < off
    off = 0 if reset else off
    with open(path, "rb") as f:
        f.seek(off)
        buf = f.read()
    end = buf.rfind(b"\n") + 1
    return buf[:end].decode("utf-8", "replace").splitlines(), off + end, reset


def empty():
    return {
        "off": 0,
        "kit_off": 0,
        "balance": None,
        "shops": [],
        "m": [],
        "units": {},
        "sold": {},
        "gone": {},
        "deals": [],
        "reviews": [],
        "rv_all": [0, 0],
        "posts": [],
    }


def update(store, P, now, cat=None, authors=()):
    """Reads the new bytes of events.jsonl and the kit log into store (in place) and prunes old rows. authors: our
    forum author names, lower case."""
    lines, store["off"], reset = _read_new(P["events"], store["off"])
    if reset:
        store.update(
            {k: v for k, v in empty().items() if k not in ("off", "kit_off", "balance")}
        )
    sidx = {s: i for i, s in enumerate(store["shops"])}
    units, sold = store["units"], store["sold"]
    for ln in lines:
        try:
            e = json.loads(ln)
            t, src, d, ts = (
                e.get("type"),
                e.get("src"),
                e.get("data") or {},
                float(e["ts"]),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        if t == "settle" and src == "market":
            shop = str(d.get("shop") or "")
            if shop not in sidx:
                sidx[shop] = len(store["shops"])
                store["shops"].append(shop)
            u = d.get("unit")
            store["m"].append([ts, d.get("sku"), int(d.get("amount") or 0), sidx[shop], int(u in units)])
            if u in units:
                sold[u] = [
                    ts,
                    int(d.get("amount") or 0),
                    int(d.get("unit_cost") or units[u][3]),
                ]
        elif t == "restock" and d.get("unit") and d["unit"] not in units:
            units[d["unit"]] = [
                d.get("sku"),
                d.get("category"),
                d.get("kind"),
                int(d.get("unit_cost") or d.get("cost") or 0),
                float(d.get("supplied_at") or ts),
                d.get("expires_at"),
            ]
        elif t == "unit_gone" and d.get("unit"):
            store["gone"].setdefault(d["unit"], ts)
        elif t == "settle" and src == "deal":
            val = (
                d.get("value")
                or ((cat or {}).get(d.get("sku")) or (None, None, None))[2]
            )
            store["deals"].append([ts, int(d.get("amount") or 0), int(val or 0)])
        elif t == "review" and src == "market" and e.get("role") == "shop" and e.get("mine") is True:
            s = d.get("stars")
            if isinstance(s, (int, float)) and not isinstance(s, bool):
                store["reviews"].append([ts, s])
                store["rv_all"] = [store["rv_all"][0] + 1, store["rv_all"][1] + s]
        elif (
            t == "forum"
            and d.get("role") == "buyer"
            and str(d.get("author") or "").lower() in authors
        ):
            store["posts"].append(ts)
    klines, store["kit_off"], _ = _read_new(P["kit"], store["kit_off"])
    for ln in klines:
        m = SUPPLY_RX.search(ln)
        if m:
            store["balance"] = int(m.group(4))
    lo = now - RETAIN_H * 3600
    store["m"] = [r for r in store["m"] if r[0] >= lo]
    store["deals"] = [r for r in store["deals"] if r[0] >= lo]
    store["reviews"] = [r for r in store["reviews"] if r[0] >= lo]
    store["posts"] = [x for x in store["posts"] if x >= lo]
    for u in [u for u, s in sold.items() if s[0] < lo]:
        sold.pop(u)
        units.pop(u, None)
        store["gone"].pop(u, None)
    for u in [u for u, g in store["gone"].items() if g < lo and u not in sold]:
        store["gone"].pop(u)
        units.pop(u, None)
    return store


def quant(xs, p):
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def pstats(xs, hours, shops, ours):
    xs = sorted(xs)
    p25, p50, p75 = quant(xs, 0.25), quant(xs, 0.5), quant(xs, 0.75)
    our = sorted(ours)
    pos = None
    if our:
        o = quant(our, 0.5)
        pos = round(sum(x < o for x in xs) / len(xs), 3)
    return {
        "n": len(xs),
        "min": xs[0],
        "p25": round(p25),
        "p50": round(p50),
        "p75": round(p75),
        "max": xs[-1],
        "mean": round(sum(xs) / len(xs)),
        "iqr_p50": round((p75 - p25) / p50, 3) if p50 else None,
        "settles_h": round(len(xs) / hours, 2),
        "n_shops": len(shops),
        "our_n": len(our),
        "our_pos": pos,
    }


def market_stats(store, cat, now, hours=24):
    lo = now - hours * 3600
    by_c, by_s = {}, {}
    for ts, sku, amt, si, mine in store["m"]:
        if ts < lo or ts >= now or amt <= 0:
            continue
        c = (cat.get(sku) or (None,))[0] or "?"
        for key, dst in ((c, by_c), (sku, by_s)):
            x = dst.setdefault(key, ([], set(), []))
            x[0].append(amt)
            x[1].add(si)
            if mine:
                x[2].append(amt)
    cats = {k: pstats(a, hours, s, o) for k, (a, s, o) in by_c.items()}
    skus = {k: pstats(a, hours, s, o) for k, (a, s, o) in by_s.items() if len(a) >= 5}
    return {"category": cats, "sku": skus}


def held(store, t):
    out = []
    for u, (sku, c, kind, cost, sup, exp) in store["units"].items():
        if sup > t or (exp and exp <= t):
            continue
        s, g = store["sold"].get(u), store["gone"].get(u)
        if (s and s[0] <= t) or (g and g <= t):
            continue
        out.append((u, sku, c, kind, cost))
    return out


def velocity(store, now, hours=24):
    v = {}
    for u, (ts, _, _) in store["sold"].items():
        if now - hours * 3600 <= ts < now and u in store["units"]:
            c = store["units"][u][1]
            v[c] = v.get(c, 0) + 1 / hours
    return v


def valuation(store, t, stats, vel):
    """Inventory value at t: p50 x P(sell before round end) - remaining storage (goods 1 %/day of cost)."""
    inv = held(store, t)
    n_cat = {}
    for x in inv:
        n_cat[x[2]] = n_cat.get(x[2], 0) + 1
    t_rem_h = max(0.0, (ROUND_END - t) / 3600)
    val = 0.0
    for u, sku, c, kind, cost in inv:
        s = stats["sku"].get(sku) or stats["category"].get(c)
        p50 = s["p50"] if s else cost
        rate = vel.get(c, 0.0) / n_cat[c]
        p_sell = 1 - math.exp(-rate * t_rem_h)
        hold_d = min(t_rem_h, 1 / rate if rate > 0 else t_rem_h) / 24
        val += p50 * p_sell - (STORAGE_PER_DAY * cost * hold_d if kind != "food" else 0)
    return val


def block_metrics(store, t0, t1, stats=None, vel=None):
    """Per-hour metrics over [t0, t1): our sales (revenue, gross profit), buyer surplus / deals, our posts, our
    shop's reviews (count rv_k, star sum rv_sum) and, with stats, the long-block value change (cash + inventory
    valuation)."""
    h = max((t1 - t0) / 3600, 1e-6)
    rev = gp = n = 0
    for u, (ts, amt, uc) in store["sold"].items():
        if t0 <= ts < t1:
            rev, gp, n = rev + amt, gp + amt - uc, n + 1
    dl = [r for r in store["deals"] if t0 <= r[0] < t1]
    rv = [s for ts, s in store.get("reviews") or [] if t0 <= ts < t1]
    out = {
        "hours": round(h, 4),
        "rev_h": rev / h,
        "gp_h": gp / h,
        "sales_h": n / h,
        "surplus_h": sum(v - a for _, a, v in dl) / h,
        "deals_h": len(dl) / h,
        "posts_h": sum(t0 <= x < t1 for x in store.get("posts") or []) / h,
        "rv_k": len(rv),
        "rv_sum": sum(rv),
    }
    if stats is not None:
        spent = sum(x[3] for x in store["units"].values() if t0 <= x[4] < t1)
        held_cost = sum(x[4] for x in held(store, t0) if x[3] != "food")
        cash = rev - spent - STORAGE_PER_DAY * held_cost * h / 24
        vel = vel if vel is not None else velocity(store, t1)
        out["value_h"] = (
            cash + valuation(store, t1, stats, vel) - valuation(store, t0, stats, vel)
        ) / h
    return out


def covered(store, t):
    """True when the store holds market rows from before t (data for a previous-day comparison)."""
    return bool(store["m"]) and store["m"][0][0] <= t


def shop_summary(store, stats, now):
    m = block_metrics(store, now - 86400, now)
    inv = held(store, now)
    by = {}
    for _, _, c, _, cost in inv:
        x = by.setdefault(c, [0, 0])
        x[0], x[1] = x[0] + 1, x[1] + cost
    stocked = {x[1] for x in store["units"].values() if x[4] >= now - 86400}
    out_share = {}
    for c in stocked:
        samples = [now - 86400 + 3600 * (i + 0.5) for i in range(24)]
        out_share[c] = round(
            sum(not any(x[2] == c for x in held(store, t)) for t in samples) / 24, 2
        )
    return {
        "balance": store["balance"],
        "rev_h": round(m["rev_h"]),
        "gp_h": round(m["gp_h"]),
        "sales_h": round(m["sales_h"], 2),
        "inventory": {c: {"units": a, "cost": b} for c, (a, b) in by.items()},
        "stockout_share": out_share,
    }


def buyer_summary(store, now):
    m = block_metrics(store, now - 86400, now)
    return {"surplus_h": round(m["surplus_h"]), "deals_h": round(m["deals_h"], 2)}


def time_info(now):
    return {
        "t_rem_h": round((ROUND_END - now) / 3600, 2),
        "gamma": round(gamma_at(now), 5),
        "h_to_supply_stop": round((SUPPLY_STOP - now) / 3600, 2),
        "h_to_freeze": round((ROUND_END - FREEZE_S - now) / 3600, 2),
    }


def load_store(path):
    d = _jload(path)
    # a store from before the review / post rows is rebuilt once from offset 0, so those rows are backfilled
    return d if isinstance(d, dict) and "m" in d and "reviews" in d else empty()


def save_store(path, store):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(store, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(tmp, path)
