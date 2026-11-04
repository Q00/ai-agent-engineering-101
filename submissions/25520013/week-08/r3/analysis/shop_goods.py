"""Seller goods demand and supply: market price stats, our shop by category, goods opportunity, restock
constraints and ranked policy action-key candidates. Round 3 (stdlib only, no network).

Usage (from r3/):
    uv run --no-project python analysis/shop_goods.py --data DIR --out FILE [--buyer-json FILE] [--policy FILE]

DIR is a read-only copy of the live week-09 folder with these relative paths:
    .r3/events.jsonl          ledger: settle/market = every buyer's settles at every shop (our shop's carry
                              unit_cost), restock/* = our supplied units (cost, list_price, supplied_at, lot),
                              unit_gone, broken/market (sku, shop, broke_at), fail/market (refund stage if any)
    .r3/own_shops.json        our shop URL(s), cross-checked against our restock unit ids
    .market/catalog.json      sku -> list_price, kind, category
    .market/demand.json       category leaves (category, item, kind, median_price)
    logs/market-kit.log       '[supply] Q x NAME for COST, balance B' lines (zipped 1:1 with our restock lots)
    logs/buyer_attempts.jsonl buyers' asks at OUR shop (no_stock outcomes = asks we could not serve)
--buyer-json defaults to analysis/buyer_surplus_20261104.json next to this file (our buyer's failure reasons).
--policy defaults to policy.json in r3/ (current values and path checks for the action keys).

Measured: everything read from the files. Inferred (flagged in the output): reachable share, added units from
stock-outs, storage fee (1 %/day of cost for goods, no storage rows in the ledger) and every expected effect.
Deterministic; no sampling (SEED is printed for the record only). No run timestamp is written.
"""

import argparse
import calendar
import collections
import json
import math
import re
import time
from pathlib import Path

SEED = 20261104
HERE = Path(__file__).resolve().parent
STORAGE_PER_DAY = (
    0.01  # agent.md 140: goods in stock pay 1 % of cost per day (policy.py goods_cap)
)
WINDOW_H = 1 / 3  # supply opens every 20 min (policy.json shop._window_note)
KST = 9 * 3600
SUPPLY_RX = re.compile(
    r"^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.+) for (\d+), balance (-?\d+)\s*$"
)
REQ_SKU_RX = re.compile(r"\((G[0-9a-f]{8})\)")
REQ_ITEM_RX = re.compile(r"I want to buy an? (.+?)\s*$")


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:  # half-written last line
                continue


def quant(xs, p):
    """Linear interpolation between order statistics (= statistics.quantiles method='inclusive')."""
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = math.floor(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def r(x, nd=0):
    if x is None:
        return None
    return int(round(x)) if nd == 0 else round(x, nd)


def kst(ts, fmt="%Y-%m-%dT%H:%M"):
    return time.strftime(fmt, time.gmtime(ts + KST))


def kst_ts(y, mo, d, h, mi):
    return calendar.timegm((y, mo, d, h, mi, 0)) - KST


def price_stats(amounts, ratios=None):
    n = len(amounts)
    p25, p50, p75 = quant(amounts, 0.25), quant(amounts, 0.5), quant(amounts, 0.75)
    out = {
        "n": n,
        "min": min(amounts),
        "p25": r(p25),
        "p50": r(p50),
        "p75": r(p75),
        "max": max(amounts),
        "mean": r(sum(amounts) / n),
        "iqr_over_p50": r((p75 - p25) / p50, 3) if p50 else None,
    }
    if ratios is not None:
        out["price_over_list_p50"] = r(quant(ratios, 0.5), 3)
    return out


def get_path(d, path):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None, False
        d = d[k]
    return d, True


def resolve_goods(pol, category):
    """policy.resolve(pol, category, 'goods') for the shop side, without importing policy.py."""
    out = dict(pol["defaults"]["goods"])
    cats = pol.get("categories") or {}
    for key in (category.split(">")[-1], category):
        if key in cats:
            out.update(cats[key])
    return out


def load(data):
    d = Path(data)
    cat = json.loads((d / ".market/catalog.json").read_text(encoding="utf-8"))
    dem = json.loads((d / ".market/demand.json").read_text(encoding="utf-8"))
    own = json.loads((d / ".r3/own_shops.json").read_text(encoding="utf-8"))
    market, restock, gone, broken, refunds = [], {}, {}, [], []
    restock_rows = collections.Counter()
    for e in jsonl(d / ".r3/events.jsonl"):
        t, s, x = e.get("type"), e.get("src"), e.get("data") or {}
        if t == "settle" and s == "market":
            market.append(dict(x, ts=e["ts"]))
        elif t == "restock":
            restock_rows[s] += 1
            restock.setdefault(
                x["unit"], x
            )  # the seller hook re-records some units: first row wins
        elif t == "unit_gone":
            gone.setdefault(x["unit"], dict(x, ts=e["ts"]))
        elif t == "broken" and s == "market":
            broken.append(x)
        elif t == "fail" and s == "market" and x.get("stage") == "refund":
            refunds.append(x)
    supply = [
        m.groups()
        for m in (
            SUPPLY_RX.match(x)
            for x in (d / "logs/market-kit.log")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        if m
    ]
    attempts = list(jsonl(d / "logs/buyer_attempts.jsonl"))
    return (
        cat,
        dem,
        own,
        market,
        restock,
        restock_rows,
        gone,
        broken,
        refunds,
        supply,
        attempts,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--buyer-json", default=str(HERE / "buyer_surplus_20261104.json"))
    ap.add_argument("--policy", default=str(HERE.parent / "policy.json"))
    a = ap.parse_args()
    (
        cat,
        dem,
        own,
        market,
        restock,
        restock_rows,
        gone,
        broken,
        refunds,
        supply,
        attempts,
    ) = load(a.data)
    pol = json.loads(Path(a.policy).read_text(encoding="utf-8"))
    bj = json.loads(Path(a.buyer_json).read_text(encoding="utf-8"))
    sp = pol["shop"]
    dem_by_item = {x["item"]: x for x in dem}
    kind_of_cat = {x["category"]: x["kind"] for x in dem}

    # ---------------- window, our shop identity
    market.sort(key=lambda x: (x["ts"], x.get("ledger") or 0))
    t0, t1 = market[0]["ts"], market[-1]["ts"]
    H = (t1 - t0) / 3600
    our_units = set(restock)
    ours = [x for x in market if x["unit"] in our_units]
    our_urls = sorted({x["shop"] for x in ours})
    first_stock = min(u["supplied_at"] for u in restock.values())
    H_our = (t1 - first_stock) / 3600
    for x in market:
        c = cat.get(x["sku"]) or {}
        x["category"], x["kind"], x["list_price"] = (
            c.get("category"),
            c.get("kind"),
            c.get("list_price"),
        )
    sold_ts = {x["unit"]: x["ts"] for x in ours}

    # ---------------- market price stats
    by_sku, by_cat = collections.defaultdict(list), collections.defaultdict(list)
    for x in market:
        by_sku[x["sku"]].append(x)
        by_cat[x["category"]].append(x)
    sku_stats = {}
    for sku in sorted(by_sku):
        xs = by_sku[sku]
        am = [x["amount"] for x in xs]
        st = price_stats(
            am, [x["amount"] / x["list_price"] for x in xs if x["list_price"]]
        )
        st.update(
            kind=xs[0]["kind"],
            category=xs[0]["category"],
            list_price=xs[0]["list_price"],
            n_shops=len({x["shop"] for x in xs}),
            n_ours=sum(x["unit"] in our_units for x in xs),
        )
        sku_stats[sku] = st
    cat_stats = {}
    for c in sorted(by_cat):
        xs = by_cat[c]
        am = [x["amount"] for x in xs]
        st = price_stats(
            am, [x["amount"] / x["list_price"] for x in xs if x["list_price"]]
        )
        shops = collections.Counter(x["shop"] for x in xs)
        top = sorted(shops.values(), reverse=True)
        n_ours = sum(x["unit"] in our_units for x in xs)
        st.update(
            kind=kind_of_cat.get(c) or xs[0]["kind"],
            settles_per_hour=r(len(xs) / H, 3),
            revenue_per_hour=r(sum(am) / H),
            n_shops=len(shops),
            n_skus=len({x["sku"] for x in xs}),
            top_shop_share=r(top[0] / len(xs), 3),
            top3_share=r(sum(top[:3]) / len(xs), 3),
            hhi=r(sum((v / len(xs)) ** 2 for v in top), 4),
            n_ours=n_ours,
            our_share=r(n_ours / len(xs), 4),
            top_shop_is_ours=shops.most_common(1)[0][0] in our_urls,
        )
        cat_stats[c] = st
    hourly = collections.OrderedDict()
    for x in market:
        h = kst(x["ts"], "%Y-%m-%dT%H")
        row = hourly.setdefault(
            h,
            {"all": 0, "goods": 0, "food": 0, "ours": 0, "ours_goods": 0, "amount": 0},
        )
        row["all"] += 1
        row[x["kind"]] += 1
        row["amount"] += x["amount"]
        if x["unit"] in our_units:
            row["ours"] += 1
            row["ours_goods"] += x["kind"] == "goods"
    hk = sorted(hourly)
    full = [hourly[h]["goods"] for h in hk[1:-1]]

    # ---------------- amount == price paid? (our buys: market settle amount vs our payment; our sales: vs the
    # price our seller accepted in buyer_attempts, matched by sku within 120 s)
    acc = sorted(
        (x["ts"], x["sku"], x["price"])
        for x in attempts
        if x.get("event") == "outcome" and x.get("outcome") == "accept" and x.get("sku")
    )
    o_by_sku = collections.defaultdict(list)
    for x in ours:
        o_by_sku[x["sku"]].append((x["ts"], x["amount"]))
    match_eq = match_n = 0
    for ts, sku, price in acc:
        c = [(abs(t - ts), am) for t, am in o_by_sku.get(sku, []) if abs(t - ts) <= 120]
        if c:
            match_n += 1
            match_eq += min(c)[1] == int(round(price))
    dpath = Path(a.data)
    pay = {x["order"]: x for x in jsonl(dpath / ".market/payments.jsonl")}
    m_by_order = {x["order"]: x for x in market}
    buys_eq = sum(
        1
        for o, p in pay.items()
        if p.get("paid")
        and o in m_by_order
        and m_by_order[o]["amount"] == p.get("price")
    )
    buys_n = sum(1 for o, p in pay.items() if p.get("paid") and o in m_by_order)

    # ---------------- our shop by category
    gcf = sorted(
        u["cost"] / u["list_price"] for u in restock.values() if u["kind"] == "goods"
    )
    cf_goods = quant(gcf, 0.5)
    broken_ours = [b for b in broken if b.get("shop") in our_urls]
    broken_ours_by_cat = collections.Counter(
        (cat.get(b["sku"]) or {}).get("category") for b in broken_ours
    )
    units_by_cat = collections.defaultdict(list)
    for u in restock.values():
        units_by_cat[u["category"]].append(u)
    sold_by_cat = collections.defaultdict(list)
    for x in ours:
        sold_by_cat[restock[x["unit"]]["category"]].append(x)
    obc = {}
    for c in sorted(set(units_by_cat) | set(sold_by_cat)):
        us, xs = units_by_cat.get(c, []), sold_by_cat.get(c, [])
        kind = us[0]["kind"] if us else xs[0]["kind"]
        held = [u for u in us if u["unit"] not in sold_ts]
        hold_h = sorted(
            (sold_ts[u["unit"]] - u["supplied_at"]) / 3600
            for u in us
            if u["unit"] in sold_ts
        )
        stor = 0.0
        if kind == "goods":
            for u in us:
                end = sold_ts.get(u["unit"], t1)
                stor += (
                    u["cost"]
                    * STORAGE_PER_DAY
                    * max(0.0, end - u["supplied_at"])
                    / 86400
                )
        expired = [u for u in held if u.get("expires_at") and u["expires_at"] < t1]
        margin = [x["amount"] - x["unit_cost"] for x in xs]
        rel = [
            x["amount"] / sku_stats[x["sku"]]["p50"]
            for x in xs
            if sku_stats[x["sku"]]["p50"]
        ]
        # time with zero units of this category in stock, from our first unit of it to t1 (sold = left stock)
        evs = sorted(
            [(u["supplied_at"], 1) for u in us]
            + [(sold_ts[u["unit"]], -1) for u in us if u["unit"] in sold_ts]
        )
        level, last, zero = 0, None, 0.0
        for ts, dlt in evs:
            if last is not None and level == 0:
                zero += ts - last
            level += dlt
            last = ts
        if level == 0 and last is not None:
            zero += t1 - last
        span = t1 - min(u["supplied_at"] for u in us) if us else 0
        mk = cat_stats.get(c, {})
        gm = sum(margin)
        obc[c] = {
            "kind": kind,
            "restocked_units": len(us),
            "sold_units": len(xs),
            "unit_gone_rows": sum(u["unit"] in gone for u in us),
            "broken_after_sale": broken_ours_by_cat.get(c, 0),
            "broken_rate_after_sale": r(broken_ours_by_cat.get(c, 0) / len(xs), 3)
            if xs
            else None,
            "held_units_end": len(held),
            "held_cost_end": sum(u["cost"] for u in held),
            "held_food_expired_units": len(expired),
            "held_food_expired_cost": sum(u["cost"] for u in expired),
            "sales_per_hour": r(len(xs) / H_our, 3),
            "market_settles": mk.get("n", 0),
            "share_of_market_settles": r(len(xs) / mk["n"], 4) if mk else None,
            "price_over_market_sku_p50": {
                "n": len(rel),
                "p50": r(quant(rel, 0.5), 3),
                "mean": r(sum(rel) / len(rel), 3) if rel else None,
            },
            "gross_margin_per_unit": {
                "n": len(margin),
                "mean": r(gm / len(margin)) if margin else None,
                "p50": r(quant(margin, 0.5)),
                "over_price": r(gm / sum(x["amount"] for x in xs), 3) if xs else None,
            },
            "revenue": sum(x["amount"] for x in xs),
            "gross_margin": gm,
            "hold_hours_sold": {
                "n": len(hold_h),
                "p25": r(quant(hold_h, 0.25), 3),
                "p50": r(quant(hold_h, 0.5), 3),
                "p75": r(quant(hold_h, 0.75), 3),
            },
            "storage_fee_est": r(stor),
            "stockout_frac_since_first_unit": r(zero / span, 3) if span else None,
            "profit_contribution_est": r(gm - stor - sum(u["cost"] for u in expired)),
        }
    tot = {
        k: sum(v[k] for v in obc.values())
        for k in (
            "restocked_units",
            "sold_units",
            "unit_gone_rows",
            "broken_after_sale",
            "held_units_end",
            "held_cost_end",
            "revenue",
            "gross_margin",
            "storage_fee_est",
            "profit_contribution_est",
            "held_food_expired_cost",
        )
    }
    by_kind_tot = {}
    for k in ("food", "goods"):
        vs = [v for v in obc.values() if v["kind"] == k]
        by_kind_tot[k] = {
            f: sum(v[f] for v in vs)
            for f in (
                "restocked_units",
                "sold_units",
                "broken_after_sale",
                "held_units_end",
                "held_cost_end",
                "revenue",
                "gross_margin",
                "storage_fee_est",
                "profit_contribution_est",
            )
        }
        n_m = sum(1 for x in market if x["kind"] == k)
        by_kind_tot[k].update(
            market_settles=n_m, share=r(by_kind_tot[k]["sold_units"] / n_m, 4)
        )
    # warranty sales of ours and breaks within the warranty (break matched to our latest earlier sale of the sku)
    w_sales = [x for x in ours if x.get("warranty_hours")]
    g_sales = [x for x in ours if x["kind"] == "goods"]
    w_share = len(w_sales) / len(g_sales) if g_sales else 0.0
    br_w = 0
    for b in broken_ours:
        prev = [x for x in ours if x["sku"] == b["sku"] and x["ts"] <= b["broke_at"]]
        if (
            prev
            and prev[-1].get("warranty_hours")
            and b["broke_at"] - prev[-1]["ts"] <= prev[-1]["warranty_hours"] * 3600
        ):
            br_w += 1

    # ---------------- own-shop asks (buyer_attempts: last part of the window only)
    att_ts = [x["ts"] for x in attempts]
    task_cat, req_cat, nostock_cat, acc_cat = (
        {},
        collections.Counter(),
        collections.Counter(),
        collections.Counter(),
    )
    for x in attempts:
        if x.get("event") == "request":
            t = x.get("text") or ""
            m = REQ_SKU_RX.search(t)
            c = (cat.get(m.group(1)) or {}).get("category") if m else None
            if c is None:
                mi = REQ_ITEM_RX.search(t)
                c = (dem_by_item.get(mi.group(1)) or {}).get("category") if mi else None
            task_cat[x["task"]] = c or "?"
            req_cat[c or "?"] += 1
    for x in attempts:
        if x.get("event") == "outcome":
            c = task_cat.get(x["task"])
            if c is None:
                t = x.get("request") or ""
                m = REQ_SKU_RX.search(t)
                mi = REQ_ITEM_RX.search(t)
                c = (
                    (cat.get(m.group(1)) or {}).get("category")
                    if m
                    else (dem_by_item.get(mi.group(1)) or {}).get("category")
                    if mi
                    else None
                ) or "?"
            if x.get("outcome") == "no_stock":
                nostock_cat[c] += 1
            elif x.get("outcome") == "accept":
                acc_cat[c] += 1
    att_h = (max(att_ts) - min(att_ts)) / 3600 if att_ts else 0
    own_attempts = {
        "window": [kst(min(att_ts)), kst(max(att_ts))] if att_ts else None,
        "hours": r(att_h, 2),
        "rows": len(attempts),
        "events": {
            f"{k[0]}:{k[1]}" if k[1] else k[0]: v
            for k, v in sorted(
                collections.Counter(
                    (x.get("event"), x.get("outcome")) for x in attempts
                ).items(),
                key=lambda kv: (str(kv[0][0]), str(kv[0][1])),
            )
        },
        "by_category": {
            c: {
                "asks": req_cat[c],
                "accept": acc_cat[c],
                "no_stock": nostock_cat[c],
            }
            for c in sorted(
                set(req_cat) | set(nostock_cat) | set(acc_cat),
                key=lambda c: (-req_cat[c], c),
            )
        },
        "note": "Asks that reached OUR seller hook only (a buyer that picked another shop never shows up); the "
        "file starts at the first row it holds, so it covers only these hours of the 22.7 h window.",
    }

    # ---------------- constraints: restock windows (lots clustered by a gap > 10 min), balance from the kit log
    lots = collections.OrderedDict()
    for u in sorted(
        restock.values(), key=lambda u: (u["supplied_at"], u["lot"], u["unit"])
    ):
        lots.setdefault(u["lot"], []).append(u)
    lot_list = list(lots.values())
    zip_ok = len(supply) == len(lot_list) and all(
        s[2] == l[0]["name"] and int(s[1]) == len(l) for s, l in zip(supply, lot_list)
    )
    windows, cur = [], []
    for l in lot_list:
        if cur and l[0]["supplied_at"] - cur[-1][0]["supplied_at"] > 600:
            windows.append(cur)
            cur = []
        cur.append(l)
    windows.append(cur)
    t_cap = kst_ts(2026, 11, 3, 21, 16)  # _cap_note: 0.12/120 -> 0.30/250
    g15 = [u["supplied_at"] for u in restock.values() if u["sku"] == "G1515d951"]
    t_v120a = min(g15) if g15 else None  # inferred: first G1515d951 unit = v1.20a live
    bal_after = {}
    if zip_ok:
        for s, l in zip(supply, lot_list):
            bal_after[l[0]["lot"]] = (int(s[4]), int(s[3]))
    max_lines = int(sp["max_lines_per_run"])

    def held_at(ts):
        return [u for u in restock.values() if u["supplied_at"] < ts and sold_ts.get(u["unit"], 1e18) >= ts
                and not (u.get("expires_at") and u["expires_at"] < ts)]

    prows, wrows = [], []
    for wi, w in enumerate(windows):
        # one plan buys each SKU once and at most max_lines lines: a repeated SKU or line max_lines + 1 starts the
        # next plan (heuristic; the kit runs the lines of consecutive plans 10 s apart with no visible boundary)
        plans, p = [], []
        for l in w:
            if p and (len(p) >= max_lines or l[0]["sku"] in {x[0]["sku"] for x in p}):
                plans.append(p)
                p = []
            p.append(l)
        plans.append(p)
        ws = w[0][0]["supplied_at"]
        span = w[-1][0]["supplied_at"] - ws
        wlen = 600 if wi == 0 else 180  # _window_note: first window 10 min, then 3 min
        wrows.append({"ts": ws, "lines": len(w), "plans": len(plans), "span_s": span,
                      "lines_fit": wlen // 10, "units": sum(len(l) for l in w),
                      "after_v120a": bool(t_v120a and ws >= t_v120a)})
        for pl in plans:
            ps = pl[0][0]["supplied_at"]
            units = [u for l in pl for u in l]
            held = held_at(ps)
            b0 = None
            if pl[0][0]["lot"] in bal_after:
                b, c0 = bal_after[pl[0][0]["lot"]]
                b0 = b + c0
            frac = 0.12 if ps < t_cap else float(sp["max_inventory_cost_frac"])
            hc = sum(u["cost"] for u in held)
            prows.append({"ts": ps, "lines": len(pl), "units": len(units),
                          "worst": sum(math.ceil(u["list_price"] * 0.75) for u in units),
                          "held_units": len(held), "held_cost": hc, "balance": b0,
                          "inv_room": frac * b0 - hc if b0 is not None else None,
                          "umax": 120 if ps < t_cap else int(sp["max_total_units"]),
                          "mspend": int(sp["max_spend_per_run"]) if (t_v120a and ps >= t_v120a) else 600000,
                          "after_v120a": bool(t_v120a and ps >= t_v120a)})

    def cnt(rows, pred):
        out = {}
        for lab, sel in (("before_v120a", False), ("after_v120a", True)):
            rs = [x for x in rows if x["after_v120a"] == sel]
            k = sum(1 for x in rs if pred(x))
            out[lab] = {"k": k, "n": len(rs), "rate": r(k / len(rs), 3) if rs else None}
        return out

    nW = len(wrows)
    span_w = (wrows[-1]["ts"] - wrows[0]["ts"]) / 1200 + 1 if nW else 0
    last_bal = int(supply[-1][4]) if supply else None
    held_end_cost = tot["held_cost_end"]
    constraints = {
        "restock_windows_seen": nW,
        "restock_plans_inferred": len(prows),
        "windows_expected_at_20min_between_first_and_last": int(round(span_w)),
        "windows_without_any_supply": int(round(span_w)) - nW,
        "supply_refused_closed_window": sum(
            1 for x in (dpath / "logs/market-kit.log").read_text(encoding="utf-8").splitlines()
            if "[supply]" in x and "supply is closed" in x),
        "kit_log_zip_ok": zip_ok,
        "policy_timeline_assumed": {
            "before_" + kst(t_cap): "max_inventory_cost_frac 0.12, max_total_units 120 (_cap_note)",
            "v120a_go_live_inferred": kst(t_v120a) if t_v120a else None,
            "max_spend_per_run": "600000 before v120a go-live (first G1515d951 unit, skus_per_category 2), "
                                 f"{sp['max_spend_per_run']} after",
            "now": {k: sp[k] for k in ("max_inventory_cost_frac", "max_total_units", "max_spend_per_run",
                                        "max_lines_per_run", "max_unit_cost", "balance_floor")},
        },
        "plan_bound_counts": {
            "max_spend_per_run_(worst>=0.95*max_spend)": cnt(prows, lambda x: x["worst"] >= 0.95 * x["mspend"]),
            "max_lines_per_run_(lines==max_lines)": cnt(prows, lambda x: x["lines"] >= max_lines),
            "inventory_cost_cap_(worst>=0.9*(frac*balance-held_cost))": cnt(
                prows, lambda x: x["inv_room"] is not None and x["worst"] >= 0.9 * x["inv_room"]),
            "max_total_units_(held+bought>=0.9*cap)": cnt(prows, lambda x: x["held_units"] + x["units"] >= 0.9 * x["umax"]),
        },
        "window_bound_counts": {
            "supply_window_time_(lines>=lines_fit-2)": cnt(wrows, lambda x: x["lines"] >= x["lines_fit"] - 2),
            "window_used_>=150s_of_180s": cnt(wrows, lambda x: x["span_s"] >= 150),
        },
        "per_plan_p50": {lab: {k: r(quant([x[k] for x in prows if x["after_v120a"] == sel], 0.5)) for k in
                               ("lines", "units", "worst", "held_units", "held_cost", "balance", "inv_room")}
                         for lab, sel in (("before_v120a", False), ("after_v120a", True))},
        "per_window_p50": {lab: {k: r(quant([x[k] for x in wrows if x["after_v120a"] == sel], 0.5)) for k in
                                 ("lines", "plans", "units", "span_s")}
                           for lab, sel in (("before_v120a", False), ("after_v120a", True))},
        "balance_last_supply_line": last_bal,
        "held_cost_end_vs_cap": {
            "held_cost_end": held_end_cost,
            "cap_0.30_x_last_balance": r(0.30 * last_bal) if last_bal else None,
            "ratio": r(held_end_cost / (0.30 * last_bal), 3) if last_bal else None,
        },
        "max_unit_cost_filter": "restock_plan drops SKUs with list x 0.75 > max_unit_cost "
                                f"({sp['max_unit_cost']}); see goods_opportunity.ranked[].share_settles_over_unit_cost_cap",
        "note": "Reconstructions. Windows = our supply lots clustered by a gap > 10 min; plans split inside a window "
                "by a repeated SKU or the 11th line (heuristic). held stock = our units supplied before the plan and "
                "not yet sold (expired food dropped); balance = the kit log balance before the plan's first line; the "
                "real plan read inventory.json at plan time. 'worst' = ceil(0.75 x list) x qty, the plan's budget "
                "unit. Windows with no supply cannot be told apart (cap full, idle, targets met).",
    }

    # ---------------- goods opportunity (inferred)
    goods_share_all = by_kind_tot["goods"]["share"]
    est_cats = [
        c for c, v in obc.items() if v["kind"] == "goods" and v["sold_units"] >= 20
    ]
    r_high = (
        quant([obc[c]["share_of_market_settles"] for c in est_cats], 0.5)
        if est_cats
        else goods_share_all
    )
    g_hold = sorted(
        h
        for c, v in obc.items()
        if v["kind"] == "goods"
        for h in [
            (sold_ts[u["unit"]] - u["supplied_at"]) / 3600
            for u in units_by_cat[c]
            if u["unit"] in sold_ts
        ]
    )
    hold_default = quant(g_hold, 0.75)
    bgoods = bj["q2_completion"]["goods_categories"]
    ucap = sp["max_unit_cost"]

    def opp_block(c, xs):
        """Inferred effect of reaching the base/high share on these settles (all, or within the unit cost cap)."""
        if not xs:
            return None
        v = len(xs) / H
        est_cost = [x["list_price"] * cf_goods for x in xs]
        m50 = quant([x["amount"] - k for x, k in zip(xs, est_cost)], 0.5)
        c50, p50 = quant(est_cost, 0.5), quant([x["amount"] for x in xs], 0.5)
        hh = obc[c]["hold_hours_sold"]["p50"] if c in obc and obc[c]["hold_hours_sold"]["n"] >= 5 else hold_default
        stor = c50 * STORAGE_PER_DAY * hh / 24
        brate = sum(broken_sku.get(s, 0) for s in sorted({x["sku"] for x in xs})) / len(xs)  # broken rows / settles
        brk = brate * w_share * p50
        unit_net = m50 - stor - brk
        n_ours = sum(x["unit"] in our_units for x in xs)
        s_now = n_ours / len(xs)
        rows = {}
        for lab, rr in (("base", goods_share_all), ("high", r_high)):
            add = max(0.0, rr - s_now) * v
            rows[lab] = {"reachable_share": r(rr, 4), "added_units_per_h": r(add, 3),
                         "added_gross_profit_per_h": r(add * unit_net), "added_revenue_per_h": r(add * p50),
                         "inventory_cost_per_window": r(add * WINDOW_H * c50)}
        return {"settles": len(xs), "settles_per_hour": r(v, 3), "p50": r(p50), "our_sold": n_ours,
                "our_share_now": r(s_now, 4), "est_unit_cost_p50": r(c50), "est_margin_p50_at_market_price": r(m50),
                "storage_per_unit": r(stor), "hold_hours_used": r(hh, 3), "break_rate": r(brate, 3),
                "break_cost_per_unit": r(brk), "net_per_unit": r(unit_net), "expected": rows}

    broken_sku = collections.Counter(b["sku"] for b in broken)
    opp = []
    for c in sorted(c for c in cat_stats if cat_stats[c]["kind"] == "goods"):
        xs = [x for x in by_cat[c] if x["list_price"]]
        cs = cat_stats[c]
        within = [x for x in xs if x["list_price"] * 0.75 <= ucap]
        bf = bgoods.get(c) or {}
        opp.append({
            "category": c, "n_shops": cs["n_shops"], "top_shop_share": cs["top_shop_share"],
            "top3_share": cs["top3_share"], "hhi": cs["hhi"],
            "share_settles_over_unit_cost_cap": r(1 - len(within) / len(xs), 3),
            "within_unit_cost_cap": opp_block(c, within),
            "all_settles": opp_block(c, xs),
            "our_buyer_goods_orders": {"orders": bf.get("orders", 0),
                                       "no_candidate_shop": (bf.get("reasons") or {}).get("no_candidate_shop", 0)},
            "own_shop_no_stock_asks": nostock_cat.get(c, 0),
        })

    def gp(o, key="within_unit_cost_cap"):
        b = o[key]
        return b["expected"]["base"]["added_gross_profit_per_h"] if b else 0

    opp.sort(key=lambda o: (-gp(o), -gp(o, "all_settles"), o["category"]))
    for i, o in enumerate(opp, 1):
        o["rank"] = i

    # ---------------- action-key candidates
    MAT = "가구/침구>가구>침대/매트리스"
    cands = []
    ta = t_v120a or first_stock
    h_after = (t1 - ta) / 3600
    plans_per_w = quant([x["plans"] for x in wrows if x["after_v120a"]], 0.5) or 0

    def stockout(c, a, b):
        """Share of [a, b] with zero units of category c in our stock (sold = left stock)."""
        us = units_by_cat.get(c, [])
        level = sum(1 for u in us if u["supplied_at"] < a and sold_ts.get(u["unit"], 1e18) >= a)
        evs = sorted([(u["supplied_at"], 1) for u in us if a <= u["supplied_at"] < b] +
                     [(sold_ts[u["unit"]], -1) for u in us if u["unit"] in sold_ts and a <= sold_ts[u["unit"]] < b])
        last, zero = a, 0.0
        for ts, dlt in evs:
            if level == 0:
                zero += ts - last
            level += dlt
            last = ts
        if level == 0:
            zero += b - last
        return zero / (b - a) if b > a else None

    def depth_effect(c, extra_units_per_h):
        """INFERRED: lost = in-stock sales rate x stock-out share (after v1.20a); credit half of it, at most the
        extra supply; x our measured mean margin / price in that period."""
        xs = [x for x in sold_by_cat.get(c, []) if x["ts"] >= ta]
        so = stockout(c, ta, t1)
        if not xs or so is None or so >= 1:
            return None
        rate_in = len(xs) / (h_after * (1 - so))
        lost = rate_in * so
        units = min(0.5 * lost, extra_units_per_h)
        mpu = sum(x["amount"] - x["unit_cost"] for x in xs) / len(xs)
        ppu = sum(x["amount"] for x in xs) / len(xs)
        return {"n_sold_after_v120a": len(xs), "hours_after_v120a": r(h_after, 2), "stockout_frac": r(so, 3),
                "in_stock_rate_per_h": r(rate_in, 2), "lost_per_h_inferred": r(lost, 2),
                "extra_supply_per_h": r(extra_units_per_h, 2), "credited_units_per_h": r(units, 2),
                "margin_per_unit": r(mpu), "price_per_unit": r(ppu),
                "profit_per_h": r(units * mpu), "revenue_per_h": r(units * ppu)}

    def add(path, proposed, rng, eff, risk, ev, new_key=False, reads=None, companions=None, effective=None):
        cur, ok = get_path(pol, path)
        row = {"path": path, "current": cur, "proposed": proposed, "range_suggestion": rng,
               "expected_effect": eff, "risk": risk, "evidence": ev}
        if effective is not None:
            row["current_effective"] = effective
        if companions:
            row["companion_changes"] = [dict(path=p, current=get_path(pol, p)[0], proposed=v) for p, v in companions]
        if new_key or not ok:
            row["new_key"] = True
            row["code_that_reads_it"] = reads
        cands.append(row)

    # (1) mattress depth per plan. Bound now: every plan after v1.20a hits max_spend_per_run (2+2 mattresses worst
    # 1,054,216); qty_per_buy 4 -> 6 (3+3) adds 2 units per plan and needs about +527k worst spend per plan.
    mat_skus = sorted({u["sku"] for u in units_by_cat.get(MAT, []) if u["supplied_at"] >= ta})
    add_worst = sum(math.ceil(cat[s]["list_price"] * 0.75) for s in mat_skus)
    extra = 2 * plans_per_w * 3                                   # +2 units/plan x plans/window x 3 windows/h
    eff = depth_effect(MAT, extra)
    mat_before = [x for x in sold_by_cat.get(MAT, []) if x["ts"] < ta]
    mk_b = sum(1 for x in by_cat[MAT] if x["ts"] < ta)
    mk_a = sum(1 for x in by_cat[MAT] if x["ts"] >= ta)
    h_before = (ta - first_stock) / 3600
    add("shop.max_spend_per_run", int(sp["max_spend_per_run"]) + int(math.ceil(add_worst / 100000) * 100000),
        [int(sp["max_spend_per_run"]), 1700000],
        dict(eff or {}, how="INFERRED (see fields): after v1.20a our mattress stock was empty stockout_frac of the "
                            "time; lost = in-stock rate x stockout_frac; half of it is credited, capped by the extra "
                            f"supply (+2 units/plan x {plans_per_w} plans/window x 3 windows/h); x our measured mean "
                            "margin and price per mattress after v1.20a."),
        "Reverses 성수님's 11-04 11:21 decision (1.5M rejected, 1.1M kept): the user must decide. Worst spend per "
        f"plan rises by about {add_worst}; inventory cap 0.30 x balance is not binding (constraints). Mattress "
        "breaks after sale are not refunded without a warranty; unsold mattresses at the round end are a loss.",
        {"max_spend_bound_plans_after_v120a": constraints["plan_bound_counts"]["max_spend_per_run_(worst>=0.95*max_spend)"]["after_v120a"],
         "mattress_before_vs_after_v120a (confounded with time of day)": {
             "before": {"hours": r(h_before, 2), "our_sold": len(mat_before),
                        "our_per_h": r(len(mat_before) / h_before, 2) if h_before else None,
                        "market_per_h": r(mk_b / h_before, 1) if h_before else None,
                        "our_share": r(len(mat_before) / mk_b, 4) if mk_b else None},
             "after": {"hours": r(h_after, 2), "our_sold": len(sold_by_cat.get(MAT, [])) - len(mat_before),
                       "our_per_h": r((len(sold_by_cat.get(MAT, [])) - len(mat_before)) / h_after, 2),
                       "market_per_h": r(mk_a / h_after, 1),
                       "our_share": r((len(sold_by_cat.get(MAT, [])) - len(mat_before)) / mk_a, 4) if mk_a else None}},
         "mattress_market": {k: cat_stats[MAT][k] for k in ("settles_per_hour", "p50", "n_shops", "top_shop_share")},
         "mattress_hold_hours_p50": obc[MAT]["hold_hours_sold"]["p50"],
         "own_shop_asks_mattress": own_attempts["by_category"].get(MAT)},
        companions=[(f"categories.{MAT}.qty_per_buy", 6), (f"categories.{MAT}.stock_target", 12)])

    # (2) per-category depth for the other established goods categories (no categories[] entry yet)
    for c in sorted(c for c, v in obc.items() if v["kind"] == "goods" and v["sold_units"] >= 20 and c != MAT):
        e2 = depth_effect(c, 1 * plans_per_w * 3)               # +1 unit/plan
        if not e2:
            continue
        g = resolve_goods(pol, c)
        add(f"categories.{c}.stock_target", g["stock_target"] + 2, [g["stock_target"], g["stock_target"] + 4],
            dict(e2, how="INFERRED: same stock-out model as candidate 1, extra supply +1 unit/plan (with "
                         "qty_per_buy +1)."),
            "Adds a categories[] block; deeper stock of a category that sells less than one unit per plan only "
            "raises held stock. Uses plan budget that mattresses also need while max_spend_per_run binds.",
            {"category_market": {k: cat_stats[c][k] for k in ("settles_per_hour", "p50", "n_shops", "top_shop_share",
                                                               "our_share")},
             "our": {k: obc[c][k] for k in ("sold_units", "stockout_frac_since_first_unit", "broken_rate_after_sale")}},
            effective=f"absent; resolves to defaults.goods stock_target {g['stock_target']}, qty_per_buy "
                      f"{g['qty_per_buy']}",
            new_key=True, reads="policy.resolve(pol, category, 'goods') overlays categories[<full path>] on "
                                "defaults.goods; restock_plan reads c['stock_target'] and c['qty_per_buy'] "
                                "(add qty_per_buy = current + 1 in the same block).")

    # (3) max_unit_cost: SKUs restock_plan never buys (list x 0.75 > cap); band up to the proposed cap
    prop_cap = 600000
    band_rows, band_gp, band_rev = [], 0.0, 0.0
    for c in sorted(c for c in cat_stats if cat_stats[c]["kind"] == "goods"):
        xs = [x for x in by_cat[c] if x["list_price"] and ucap < x["list_price"] * 0.75 <= prop_cap]
        if not xs:
            continue
        b = opp_block(c, xs)
        e = b["expected"]["base"]
        band_gp += e["added_gross_profit_per_h"]
        band_rev += e["added_revenue_per_h"]
        band_rows.append({"category": c, "band_settles": b["settles"], "settles_per_hour": b["settles_per_hour"],
                          "p50": b["p50"], "net_per_unit": b["net_per_unit"], "added_gross_profit_per_h": e["added_gross_profit_per_h"]})
    band_rows.sort(key=lambda x: (-x["added_gross_profit_per_h"], x["category"]))
    add("shop.max_unit_cost", prop_cap, [int(ucap), 700000],
        {"profit_per_h": r(band_gp), "revenue_per_h": r(band_rev),
         "how": f"INFERRED. Settles of SKUs with {ucap} < list x 0.75 <= {prop_cap} (never bought by restock_plan "
                f"now): settles/h x reachable share {goods_share_all} (our share of all goods settles) x (p50 - list x "
                f"{cf_goods:.3f} - storage - break); summed over goods categories."},
        "One unit ties up up to 600k (worst cost) of the 1.1M plan budget, so it competes with mattress lines "
        "(candidate 1); a warranty break refunds the full price; few SKUs per band.",
        {"band_by_category": band_rows[:6]})

    # (4) goods categories to rank first (priority reorders only; lines per window are nearly all used)
    food_lots = [l for l in lot_list if l[0]["kind"] == "food"]
    food_gm_per_line = by_kind_tot["food"]["gross_margin"] / len(food_lots) if food_lots else 0
    # categories we already stock are left out: their stock was rarely empty (e.g. bedding), so a low share there is
    # not from missing stock and reordering cannot raise it (see our_shop_by_category.stockout_frac_since_first_unit)
    new_cats = [o for o in opp if o["within_unit_cost_cap"] and o["within_unit_cost_cap"]["our_sold"] == 0
                and o["category"] not in obc]
    top = [o for o in new_cats
           if o["within_unit_cost_cap"]["expected"]["base"]["added_gross_profit_per_h"] > 3 * food_gm_per_line][:2]
    best_new = [{"category": o["category"], **{k: o["within_unit_cost_cap"][k] for k in (
        "settles_per_hour", "p50", "net_per_unit")}, "added_gross_profit_per_h":
        o["within_unit_cost_cap"]["expected"]["base"]["added_gross_profit_per_h"]} for o in new_cats[:3]]
    cur_prio = list(sp.get("restock_priority") or [])
    g_add = sum(o["within_unit_cost_cap"]["expected"]["base"]["added_gross_profit_per_h"] for o in top)
    g_rev = sum(o["within_unit_cost_cap"]["expected"]["base"]["added_revenue_per_h"] for o in top)
    displaced = 3 * food_gm_per_line * len(top)
    add("shop.restock_priority", cur_prio + [o["category"] for o in top if o["category"] not in cur_prio],
        "mattress first, then 0-2 goods categories",
        {"profit_per_h": r(g_add - displaced), "revenue_per_h": r(g_rev),
         "how": "INFERRED. goods_opportunity within_unit_cost_cap base for goods categories we never stocked, minus one "
                "displaced food line per window each; a category is listed only when its base gain beats that "
                f"(3 x {r(food_gm_per_line)} per h). Listed: {len(top)}. Base gain "
                f"= settles/h x share {goods_share_all} x net per unit; food gross margin per line {r(food_gm_per_line)}"
                f" (n={len(food_lots)} food lines); assumption: windows are time-bound, constraints.window_bound_counts."},
        "Priority reorders only; depth still comes from defaults.goods (stock_target 4, qty_per_buy 3). The model "
        "assumes we sell at the market p50 and win the base share against incumbents.",
        {"best_unstocked_categories": best_new,
         "bedding_stockout_frac": (obc.get("가구/침구>침구/홈데코>침구") or {}).get("stockout_frac_since_first_unit"),
         "categories": [{"category": o["category"], "top_shop_share": o["top_shop_share"],
                         **{k: o["within_unit_cost_cap"][k] for k in ("settles_per_hour", "p50", "our_share_now",
                                                                       "net_per_unit")}} for o in top]})

    # (5) caps that do not bind now: keep
    add("shop.max_inventory_cost_frac", sp["max_inventory_cost_frac"], [0.2, 0.3],
        {"profit_per_h": 0, "revenue_per_h": 0,
         "how": "No change proposed: the reconstructed cost cap was near-binding in "
                f"{constraints['plan_bound_counts']['inventory_cost_cap_(worst>=0.9*(frac*balance-held_cost))']['after_v120a']} "
                "plans after v1.20a; held stock is a small part of the cap (held_cost_end_vs_cap). Candidate 1 raises "
                "held cost by at most one plan's extra mattresses."},
        "Lowering it toward 0.12 would bind again at the current balance (held cost + one plan worst spend).",
        {"held_cost_end_vs_cap": constraints["held_cost_end_vs_cap"]})
    add("shop.max_total_units", sp["max_total_units"], [120, 250],
        {"profit_per_h": 0, "revenue_per_h": 0, "how": "No change proposed: never near-binding (plan_bound_counts)."},
        "None at the current depth.", {"held_units_end": tot["held_units_end"]})

    cands.sort(key=lambda x: (-(x["expected_effect"].get("profit_per_h") or 0), x["path"]))
    for i, x in enumerate(cands, 1):
        x["rank"] = i

    out = {
        "seed": SEED,
        "inputs": {
            "market_settles": len(market),
            "market_shops": len({x["shop"] for x in market}),
            "market_buyers": len({x["buyer"] for x in market}),
            "window": [kst(t0), kst(t1)],
            "hours": r(H, 2),
            "our_hours_since_first_stock": r(H_our, 2),
            "our_shop_urls": len(our_urls),
            "our_market_settles": len(ours),
            "our_settles_identified_by": "unit id in our restock rows (all on one shop URL, = own_shops.json urls: "
            f"{sorted(own.get('urls') or []) == our_urls})",
            "restock_rows": dict(sorted(restock_rows.items())),
            "restock_units": len(restock),
            "restock_lots": len(lot_list),
            "unit_gone_rows": len(gone),
            "broken_market_rows": len(broken),
            "broken_at_our_shop": len(broken_ours),
            "refund_rows": len(refunds),
            "buyer_attempts_rows": len(attempts),
            "kit_supply_lines": len(supply),
            "catalog_skus": len(cat),
            "demand_leaves": len(dem),
        },
        "amount_is_price_paid": {
            "our_buys": {"n": buys_n, "market_amount_eq_payment_price": buys_eq},
            "our_sales": {
                "n_matched_accepts": match_n,
                "amount_eq_accepted_price": match_eq,
                "match": "buyer_attempts accept (sku, price) to the nearest market settle of that sku at our shop within "
                         "120 s; a mismatch can be an accept that never settled paired with another sale of the "
                         "same SKU (not verified)",
            },
            "conclusion": "amount = the price paid: exact for all of our buys (market settle vs our payment row); for "
                          "our sales the nearest-pair match is approximate (see match). No fee field in the row.",
        },
        "market_price_stats": {
            "price_field": "amount",
            "sku": sku_stats,
            "category": cat_stats,
            "hourly_volume_kst": hourly,
            "hourly_goods_full_hours": {
                "n_hours": len(full),
                "min": min(full) if full else None,
                "p50": r(quant(full, 0.5)),
                "max": max(full) if full else None,
                "note": "first and last clock hours are partial and left out",
            },
        },
        "our_shop_totals": dict(
            tot,
            by_kind=by_kind_tot,
            goods_cost_over_list_p50={"n": len(gcf), "p50": r(cf_goods, 3)},
            goods_sales_with_warranty={
                "n_goods_sales": len(g_sales),
                "with_warranty": len(w_sales),
                "breaks_within_warranty_matched": br_w,
            },
        ),
        "our_shop_by_category": obc,
        "our_shop_asks": own_attempts,
        "goods_opportunity": {
            "model": "INFERRED. added units/h = max(0, reachable share - our share now) x market settles/h; net per "
            "unit = median over the category's settles of (amount - list x c) - storage - break; c = our "
            f"goods cost/list p50 {cf_goods:.3f} (n={len(gcf)} goods units, grade mix as supplied); storage "
            f"= cost p50 x {STORAGE_PER_DAY}/day x hold hours (category's own p50 when we sold >= 5 there, "
            f"else our goods p75 {hold_default:.3f} h); break = market broken rows / goods settles of the "
            f"category x {w_share:.3f} (share of our goods sales with a warranty, the only ones refunded) x "
            "p50. reachable share base = our share of all goods settles "
            f"({goods_share_all}); high = median share in our goods categories with >= 20 sales "
            f"({r(r_high, 4)}, n={len(est_cats)}). inventory_cost_per_window = added units/h x 1/3 h x cost. Ranked by within_unit_cost_cap.expected.base "
                     f"(settles of SKUs restock_plan can buy: list x 0.75 <= max_unit_cost {ucap}); all_settles "
                     "is the same model without that filter.",
            "ranked": opp,
            "our_buyer_note": "our_buyer_goods_orders come from analysis/buyer_surplus_20261104.json q2 "
            "goods_categories; our buyer's order mix is set by the market and is not representative "
            "of other buyers' demand.",
        },
        "constraints": constraints,
        "action_key_candidates": cands,
        "caveats": [
            "Unmet demand of other buyers is unobserved: the ledger has settles only; a buyer who found no stock "
            "anywhere leaves no row. Our own buyer's no_candidate_shop counts and our seller's no_stock asks are the "
            "only stock-out signals, and the latter covers only the buyer_attempts window.",
            f"One {H:.1f} h window (one Tuesday evening to Wednesday evening): no weekly or end-of-round pattern; "
            "volume may change as shops join or leave.",
            "Reachable share is an assumption (our current overall goods share or our median established-category "
            "share); incumbents (top_shop_share) may defend share by price.",
            "Unit cost for categories we never stocked is list x our goods cost/list p50; the grade (and cost) is "
            "drawn at supply, so the real cost varies by +-0.15 x list.",
            "Storage is estimated at 1 %/day of cost for goods from holding time; no storage rows exist in the ledger "
            "to confirm the charge. Food is not charged here (policy/aggregates convention).",
            "Break cost: no refund rows exist in the ledger; a refund is assumed only for sales with a warranty "
            "(receipt text: the market refunds the full price within 6 h), and who bears it is unconfirmed.",
            "Stock-out based effects assume buyers who arrived while we were out of stock would have bought from us at "
            "the in-stock rate; half of it is credited. This is an inferred counterfactual.",
            "Market price stats include our own sales and our own buyer's purchases (own_owner rows).",
            "Before/after v1.20a (go-live inferred from the first G1515d951 unit) is not an experiment: the after "
            "period is one afternoon with higher market mattress volume, so the rise in our mattress sales is only "
            "partly attributable to the policy change.",
            "Restock plans are split from the supply lines by a heuristic (repeated SKU or 11th line); the "
            "policy values in force before v1.20a are taken from policy.json notes, not from a log.",
        ],
    }
    text = json.dumps(out, ensure_ascii=False, indent=1)
    Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(json.dumps(out["inputs"], ensure_ascii=False))
    print(
        json.dumps(
            {k: v for k, v in out["our_shop_totals"].items()}, ensure_ascii=False
        )
    )
    print(json.dumps([constraints["plan_bound_counts"], constraints["window_bound_counts"]], ensure_ascii=False))
    for o in opp[:8]:
        w = o["within_unit_cost_cap"] or {}
        print(o["rank"], o["category"], w.get("settles_per_hour"), w.get("p50"), w.get("our_share_now"),
              w.get("net_per_unit"), (w.get("expected") or {}).get("base"))
    for c in cands:
        print(c["rank"], c["path"], c["current"], "->", c["proposed"], c["expected_effect"].get("profit_per_h"),
              c["expected_effect"].get("revenue_per_h"))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
