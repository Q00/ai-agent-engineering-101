"""Read-only replay of every shop-b-era restock window (from 11-09 12:15:56 KST), counting every 1M+ unit from both passes.
Mirrors restock_policy.plan: food trial, then shop b's pass rebuilt per SKU from the logged band (slots, eligible sales, cap), then
the ordinary pass. Inputs as logged (budget, capacity, own profitable, quality, hints, food picks, sales memory, cursor = log index),
intelligence from the archived market-intel snapshot, owned stock rebuilt from today's inventory, completed sales and kit-log supply.
Mode A (validation): the bypass set as logged, no shared cap; compared with the logged buys. Mode B: bypass = confirmed_skus at that
time, shared cap on. Run from week-08: p2_replay2.py <out.json>. Aggregates and SKU codes only."""
import json, os, sys
from collections import Counter
from pathlib import Path
here = Path(__file__).parent
src = (here / "hi_band_study.py").read_text(encoding="utf-8").split("def trace(")[0]
src = src.replace("INSTALL = 1794095351.0", "B_ON = 1794194156.0\nINSTALL = B_ON").replace("lo = INSTALL - 86400 - 3600", "lo = B_ON - 86400 - 3600")
exec(src)
import math
import median_bypass

CAP_DEFAULT = 12000000


def is_goods_hi(s):
    v = cat.get(s) or {}
    return v.get("kind") == "goods" and (v.get("list_price") or 0) >= HI


def trace2(t, ex, sales, cursor, owned, bypass, fix):
    limits = ex["capacity"]; rate = 0.01
    budget = ex["budget"]; own_profitable = ex.get("own_profitable_sales_6h") or {}
    quality = ex.get("quality_evidence") or {}; hints = ex.get("forum_hints") or {}; leader = ex.get("leader_hints") or {}
    snap, _ = intel_at(t)
    intelligence = summarize(snap.get("rows", []), cat, t) if snap else {}
    max_lines = limits.get("max_lines_per_run", limits["max_units_per_run"])
    def signal(s): return intelligence.get(s, {})
    def proven(s):
        x = signal(s)
        return bool(sales.get(s, 0) or own_profitable.get(s, 0) or (x.get("distinct_buyers", 0) >= 3 and x.get("settles_6h", 0) >= 3))
    def bulk(s):
        x = signal(s)
        return own_profitable.get(s, 0) >= limits["min_profitable_own_sales_6h"] or (x.get("distinct_buyers_6h", 0) >= limits["min_public_buyers"] and x.get("settles_6h", 0) >= limits["min_public_sales_6h"])
    def lim(s):
        if quality.get(s, {}).get("weak"): return 1
        return limits["max_proven_units_per_sku"] if bulk(s) else 1
    band = ex.get("hi_band") or {}
    eligible = band.get("eligible") or {}
    slots = band.get("slots", 10)
    hi_cap = band.get("worst_cost_cap", CAP_DEFAULT)
    hb_on = bool(eligible)
    units_total = sum(owned.values())
    held_hi = sum(n for s, n in owned.items() if is_goods_hi(s))
    reserved_held = min(held_hi, slots) if hb_on else 0
    hi_room = slots - reserved_held if hb_on else 0
    counted = units_total - reserved_held
    hi_worst = sum(cat[s]["list_price"] * .75 * n for s, n in owned.items() if is_goods_hi(s))
    held_worst = hi_worst
    stale = ex.get("stale_stock"); experimental = ex.get("unproven_stock", 0)
    xslots = 0 if stale else max(0, limits["max_unproven_units"] - experimental)
    cands = [(s, v) for s, v in sorted(cat.items()) if owned[s] < lim(s) and v.get("category") in categories and type(v.get("list_price")) in (int, float) and v["list_price"] > 0]
    if cands:
        o = cursor % len(cands); cands = cands[o:] + cands[:o]
    stocked = {cat.get(s, {}).get("category") for s in owned}
    def opp(pair):
        s, p = pair; x = signal(s); cost = p["list_price"] * .75
        price = min(p["list_price"], x.get("median_price", p["list_price"])); gap = x.get("observed_gap_hours")
        hours = min(24, max(1, gap)) if type(gap) in (int, float) and math.isfinite(gap) else 24
        margin = price - cost - cost * rate * hours / 24
        f = .9 if quality.get(s, {}).get("weak") else 1.05 if quality.get(s, {}).get("strong") else 1
        return (not proven(s), owned[s] > 0, p.get("category") in stocked, -margin / cost / hours * f, -sales.get(s, 0), -leader.get(s, 0), -hints.get(s, 0))
    cands.sort(key=opp)
    est = stor = pu = 0; planned = Counter(); hi_taken = set()
    run_room = min(limits["max_units_per_run"], limits["max_total_units"] - counted)
    for pick in ex.get("food_trial_picks") or ():
        if pick["sku"] not in (ex.get("food_trial") or ()): continue
        if pu >= run_room or est + stor + pick["estimated_cost"] > budget: continue
        planned[pick["sku"]] = 1; est += pick["estimated_cost"]; pu += 1
    # shop b's pass
    for s in sorted(eligible, key=lambda k: (-eligible[k], k)):
        v = cat.get(s) or {}
        if hi_room <= 0: break
        if not is_goods_hi(s) or v.get("category") not in categories or (s in surge_now and (owned[s] or planned[s])): continue
        worst = v["list_price"] * .75
        ref = signal(s).get("median_price")
        if s in surge_now and ref is not None and ref <= worst * (1 + rate): continue
        carry = worst * rate * 7
        q = min(lim(s) - owned[s] - planned[s], hi_room, 1 if s in surge_now else hi_room)
        while q > 0 and (est + stor + q * (worst + carry) > budget or hi_worst + q * worst > hi_cap): q -= 1
        if q <= 0: continue
        planned[s] += q; est += worst * q; stor += carry * q; hi_worst += worst * q; hi_room -= q; hi_taken.add(s)
    b_units = sum(planned[s] for s in hi_taken)
    new_range = [p for p in cands if owned[p[0]] == 0 and proven(p[0])]
    ord_hi = 0
    for position, (s, v) in enumerate(new_range + cands):
        if v.get("kind") == "food": continue
        if not proven(s) and (xslots <= 0 or counted + pu >= 16): continue
        if not planned[s] and len([k for k in planned if k not in hi_taken]) >= max_lines: continue
        if s in surge_now and (owned[s] or planned[s]): continue
        worst = v["list_price"] * .75
        ref = signal(s).get("median_price")
        if ref is not None and ref <= worst * (1 + rate):
            if s not in bypass or s in surge_now: continue
        carry = worst * rate * 7
        q = min(lim(s) - owned[s] - planned[s], limits["max_units_per_run"] - pu, limits["max_total_units"] - counted - pu)
        if position < len(new_range): q = min(q, 1)
        hi_s = is_goods_hi(s)
        while q > 0 and (est + stor + q * (worst + carry) > budget or (fix and hi_s and hi_worst + q * worst > hi_cap)): q -= 1
        if q <= 0: continue
        planned[s] += q; est += worst * q; stor += carry * q; pu += q
        if hi_s: hi_worst += worst * q; ord_hi += q
        if not proven(s): xslots -= q
        if pu >= min(limits["max_units_per_run"], limits["max_total_units"] - counted): break
    return {"planned": {k: q for k, q in planned.items() if q}, "hi_worst": hi_worst, "held_worst": held_worst, "b_units": b_units, "ord_hi": ord_hi,
            "est": est, "budget": budget, "counted_after": counted + pu}


rows = []
for i, t, f in runs:
    if t < B_ON: continue
    x = load_run(f); ex = x["explanation"]
    if ex.get("reason") != "bounded_experiment": continue
    owned = owned_at(t)
    logged = {b["sku"]: b["qty"] for b in x.get("buy") or []}
    a = trace2(t, ex, x.get("sales_memory") or {}, i, owned, set(ex.get("median_bypass_skus") or ()), False)
    bset = set(median_bypass.confirmed_skus(MD, t))
    b = trace2(t, ex, x.get("sales_memory") or {}, i, owned, bset, True)
    non_hi = lambda p: {k: q for k, q in p.items() if not is_goods_hi(k) and (cat.get(k) or {}).get("kind") == "goods"}
    rows.append({"t": d.datetime.fromtimestamp(t, d.timezone(d.timedelta(hours=9))).strftime("%H:%M:%S"),
                 "logged_bypass_n": len(ex.get("median_bypass_skus") or ()), "A_match": a["planned"] == logged,
                 "logged_hi_units": sum(q for k, q in logged.items() if is_goods_hi(k)), "A_hi_units": sum(q for k, q in a["planned"].items() if is_goods_hi(k)),
                 "A_hi_worst": round(a["hi_worst"]), "logged_band_worst": (ex.get("hi_band") or {}).get("worst_cost_total"),
                 "held_worst": round(b["held_worst"]), "B_hi_worst": round(b["hi_worst"]), "B_hi_units": sum(q for k, q in b["planned"].items() if is_goods_hi(k)),
                 "B_b_units": b["b_units"], "B_ordinary_hi": b["ord_hi"],
                 "logged_nonhi_units": sum(non_hi(logged).values()), "logged_nonhi_skus": len(non_hi(logged)),
                 "B_nonhi_units": sum(non_hi(b["planned"]).values()), "B_nonhi_skus": len(non_hi(b["planned"])),
                 "B_within_budget": b["est"] <= b["budget"], "B_counted_after": b["counted_after"]})
for r in rows: print(r)
n = len(rows)
summ = {"windows": n, "A_match_share": round(sum(r["A_match"] for r in rows) / n, 3),
        "B_max_hi_worst_when_held_under_cap": max((r["B_hi_worst"] for r in rows if r["held_worst"] <= CAP_DEFAULT), default=None),
        "B_any_addition_when_held_over_cap": any(r["B_hi_worst"] > r["held_worst"] for r in rows if r["held_worst"] > CAP_DEFAULT),
        "B_extra_nonhi_units_per_window": round(sum(r["B_nonhi_units"] - r["logged_nonhi_units"] for r in rows) / n, 2),
        "B_extra_nonhi_skus_per_window": round(sum(r["B_nonhi_skus"] - r["logged_nonhi_skus"] for r in rows) / n, 2),
        "B_all_within_budget": all(r["B_within_budget"] for r in rows), "B_max_counted_after": max(r["B_counted_after"] for r in rows)}
print("SUMMARY", json.dumps(summ))
Path(sys.argv[1]).write_text(json.dumps({"summary": summ, "rows": rows}, indent=1), encoding="utf-8")
