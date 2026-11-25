"""Read-only: our buyer deals' share distribution (.market/buyer-deals), R3 and last 24 h; deals per hour. Aggregates only."""
import json, glob, collections, statistics as st, time
cat = json.load(open(".market/catalog.json", encoding="utf-8-sig"))
D = [json.load(open(f, encoding="utf-8")) for f in glob.glob(".market/buyer-deals/*.json")]
now = time.time()
def s_val(x):
    if x.get("fault"): return 0.0 if x.get("refund_confirmed") else -1.0
    return (x["value_at_purchase"] - x["price"]) / x["value_at_purchase"]
def s_list(x):
    if x.get("fault"): return 0.0 if x.get("refund_confirmed") else -1.0
    lp = (cat.get(x["sku"]) or {}).get("list_price")
    return (lp - x["price"]) / lp if lp else None
def bands(v):
    c = collections.Counter("useless" if s == -1 else "refund" if s == 0 else "<.30" if s < .30 else "[.30,.31)" if s < .31 else "[.31,.32)" if s < .32 else "[.32,.35)" if s < .35 else "[.35,.40)" if s < .40 else ">=.40" for s in v)
    return {k: {"n": c[k], "pct": round(100 * c[k] / len(v), 1)} for k in ("useless", "refund", "<.30", "[.30,.31)", "[.31,.32)", "[.32,.35)", "[.35,.40)", ">=.40")}
def q(v):
    v = sorted(v); return {f"p{int(p*100)}": round(v[min(len(v)-1, int(len(v) * p))], 4) for p in (.05, .1, .25, .5, .75, .9)}
out = {"deals_local": len(D), "first_purchase": time.strftime("%m-%d %H:%M", time.gmtime(min(x["purchased_at"] for x in D) + 9*3600))}
for name, since in (("r3_all", 0), ("last_24h", now - 86400)):
    xs = [x for x in D if x["purchased_at"] >= since]
    for basis, f in (("value_at_purchase", s_val), ("catalog_list", s_list)):
        v = [f(x) for x in xs if f(x) is not None]
        out[f"{name} {basis}"] = {"n": len(v), "mean": round(st.mean(v), 4), "saved_formula_sum_over_n_plus_100": round(sum(v) / (len(v) + 100), 4) if name == "r3_all" else None,
                                  "quantiles": q(v), "bands": bands(v),
                                  "in_.30_.32_pct": round(100 * sum(.30 <= s < .32 for s in v) / len(v), 1)}
    kinds = collections.defaultdict(list)
    for x in xs: kinds[(cat.get(x["sku"]) or {}).get("kind")].append(s_val(x))
    out[f"{name} by_kind"] = {k: {"n": len(v), "mean": round(st.mean(v), 4), "in_.30_.32_pct": round(100 * sum(.30 <= s < .32 for s in v) / len(v), 1)} for k, v in kinds.items()}
for h in (1, 3, 6, 24):
    out[f"deals_per_h_last_{h}h"] = round(sum(x["purchased_at"] >= now - h * 3600 for x in D) / h, 1)
out["value_vs_list_differs"] = sum(1 for x in D if (cat.get(x["sku"]) or {}).get("list_price") not in (None, x["value_at_purchase"]))
print(json.dumps(out, indent=1))
