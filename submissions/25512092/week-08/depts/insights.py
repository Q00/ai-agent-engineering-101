"""Daily insights (person's decision 11/07 ~02:00, bundle 2 D), written with the daily report (depts/qc.py):

  16 one_star     my 1-star results of the day by cause (review text keywords; no text -> 'no text')
  17 top_shops    the 10 shops with the most sales that day: category mix (top 3 leaves), price vs the SKU's
                  market median of the day, warranty share; mine for comparison -> reports/top_shops_YYYYMMDD.json,
                  which the analyst reads (depts/analyst_brain.collect) as data
"""
import json
import os
import re
import statistics

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS = os.path.join(HERE, "reports")
ME = "choo-chu"
CAUSES = [("settled, no complaint", r"^payment settled; no unobserved"),   # a 1-star with a neutral text (11/07)
          ("stock / wrong item", r"no stock|out of stock|none of that|sold out|wrong|not what"),
          ("price / no deal", r"no deal|too (high|expensive)|price|budget|walked|reject"),
          ("slow / timeout", r"time ?out|timed out|slow|no answer|did not respond|took too long"),
          ("grade / quality / broken", r"grade|quality|broken|broke|damag|defect|claim"),
          ("receipt / signature", r"receipt|signature|sig_|verify|unverified"),
          ("shelf life", r"shelf|expir|hours? left")]


def one_star(db, t0, t1):
    rows = db.execute("SELECT stars, review, has_receipt FROM my_results WHERE ts>=? AND ts<? AND stars<=1",
                      (t0, t1)).fetchall()
    out, examples = {}, {}
    for _, text, _ in rows:
        t = (text or "").strip()
        kind = "no text" if not t else next((k for k, p in CAUSES if re.search(p, t, re.I)), "other")
        out[kind] = out.get(kind, 0) + 1
        if t and len(examples.setdefault(kind, [])) < 2:
            examples[kind].append(t[:120])
    total = db.execute("SELECT COUNT(*) FROM my_results WHERE ts>=? AND ts<?", (t0, t1)).fetchone()[0]
    return {"n": len(rows), "results": total, "causes": dict(sorted(out.items(), key=lambda kv: -kv[1])),
            "examples": examples}


def one_star_line(o):
    if not o["n"]:
        return f"1-star results 0 of {o['results']}"
    return (f"1-star results {o['n']} of {o['results']}: "
            + ", ".join(f"{k} {v}" for k, v in o["causes"].items()))


def top_shops(db, t0, t1, catalog, day):
    rows = db.execute("SELECT shop, sku, amount, warranty_hours FROM settles WHERE ts>=? AND ts<? AND shop IS NOT NULL",
                      (t0, t1)).fetchall()
    by_sku = {}
    for _, sku, amt, _ in rows:
        by_sku.setdefault(sku, []).append(amt)
    med = {k: statistics.median(v) for k, v in by_sku.items() if len(v) >= 3}
    leaf = lambda k: ((catalog.get(k) or {}).get("category") or "").split(">")[-1] or "?"
    shops = {}
    for shop, sku, amt, wh in rows:
        s = shops.setdefault(shop, {"sales": 0, "revenue": 0, "warranty": 0, "cats": {}, "ratios": []})
        s["sales"] += 1
        s["revenue"] += amt or 0
        s["warranty"] += 1 if wh else 0
        s["cats"][leaf(sku)] = s["cats"].get(leaf(sku), 0) + 1
        if sku in med and med[sku]:
            s["ratios"].append(amt / med[sku])
    rank = sorted(shops, key=lambda k: -shops[k]["sales"])

    def row(name):
        s = shops[name]
        return {"shop": name, "rank": rank.index(name) + 1, "sales": s["sales"], "revenue": s["revenue"],
                "top_categories": [[c, round(n / s["sales"], 2)] for c, n in
                                   sorted(s["cats"].items(), key=lambda kv: -kv[1])[:3]],
                "categories": len(s["cats"]),
                "price_vs_median": round(statistics.median(s["ratios"]), 3) if s["ratios"] else None,
                "warranty_share": round(s["warranty"] / s["sales"], 2)}
    out = {"day": f"{day:%Y-%m-%d}", "window": [t0, t1], "shops": len(shops),
           "note": "price_vs_median: median of (sale price / that SKU's median price that day, all shops)",
           "top10": [row(n) for n in rank[:10]], "mine": row(ME) if ME in shops else None}
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, f"top_shops_{day:%Y%m%d}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out


def top_shops_md(o):
    lines = ["", "## Top 10 shops (sales) vs mine", "",
             "| # | shop | sales | categories | top 3 categories | price / median | warranty |", "|---|---|---|---|---|---|---|"]
    for r in o["top10"] + ([o["mine"]] if o.get("mine") and o["mine"] not in o["top10"] else []):
        lines.append(f"| {r['rank']} | {r['shop']} | {r['sales']} | {r['categories']} | "
                     + ", ".join(f"{c} {s:.0%}" for c, s in r["top_categories"])
                     + f" | {r['price_vs_median']} | {r['warranty_share']:.0%} |")
    return lines


def latest_top_shops():
    """The newest top_shops file, compact, for the analyst (data, not instructions)."""
    import glob
    files = sorted(glob.glob(os.path.join(REPORTS, "top_shops_*.json")))
    if not files:
        return None
    o = json.load(open(files[-1], encoding="utf-8"))
    return {"day": o["day"], "top10": o["top10"], "mine": o["mine"], "note": o["note"]}


def grade_detail(db, t0, t1, catalog):
    """1-star results whose text matches the grade / quality / broken keywords (the raw bucket, as first reported),
    split into the neutral 'Payment settled; no unobserved quality or failure claims' texts and real complaints;
    each matched to that buyer's latest sale from my shop in the 15 min before (no order id in settles), then
    non-food sold without a warranty by grade."""
    pat = r"grade|quality|broken|broke|damag|defect|claim"
    stock = r"no stock|out of stock|none of that|sold out|wrong|not what"
    rows = [r for r in db.execute("SELECT ts, buyer, review FROM my_results WHERE ts>=? AND ts<? AND stars<=1",
                                  (t0, t1)) if r[2] and re.search(pat, r[2], re.I) and not re.search(stock, r[2], re.I)]
    out = {"raw": len(rows), "neutral": 0, "neutral_buyers": {}, "complaints": 0, "unmatched": 0,
           "nonfood_no_warranty": {"A": 0, "B": 0, "C": 0}, "food": 0, "with_warranty": 0}
    for ts, buyer, text in rows:
        if re.match(r"payment settled; no unobserved", text, re.I):
            out["neutral"] += 1
            out["neutral_buyers"][buyer] = out["neutral_buyers"].get(buyer, 0) + 1
        else:
            out["complaints"] += 1
        s = db.execute("SELECT sku, grade, warranty_hours FROM settles WHERE shop=? AND buyer=? AND ts<=? AND ts>=? "
                       "ORDER BY ts DESC LIMIT 1", (ME, buyer, ts + 5, ts - 900)).fetchone()
        if not s:
            out["unmatched"] += 1
        elif (catalog.get(s[0]) or {}).get("kind") == "food":
            out["food"] += 1
        elif s[2]:
            out["with_warranty"] += 1
        elif s[1] in out["nonfood_no_warranty"]:
            out["nonfood_no_warranty"][s[1]] += 1
    return out


def grade_detail_text(o, label):
    nw = o["nonfood_no_warranty"]
    nb = ", ".join(f"{b} {n}" for b, n in sorted(o["neutral_buyers"].items(), key=lambda kv: -kv[1])) or "-"
    return (f"{label} 1점 '등급/품질/고장' 키워드 {o['raw']}건: 그중 '정산 완료, 불만 없음' 문구 {o['neutral']}건 ({nb}), "
            f"실제 불만 {o['complaints']}건" + chr(10) +
            f"판매와 연결: 보증 없이 판 비식품 {sum(nw.values())}건 (A {nw['A']}, B {nw['B']}, C {nw['C']}), "
            f"식품 {o['food']}, 보증 있음 {o['with_warranty']}, 연결 안 됨 {o['unmatched']}")
