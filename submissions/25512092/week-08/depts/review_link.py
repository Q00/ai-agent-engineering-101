"""Buyers' reviews of my shop linked to my negotiations by buyer and time (person's request 11/08 ~11:20), read-only.

A result without a receipt carries no task id, and a negotiation does not know its buyer (the market does not pass
the buyer's name to the shop). A result comes 2-9 s after the negotiation's last message (90% of 673 receipted
results, 11/07-11/08), and about 6 negotiations end without a receipt a minute. So for each no-receipt result at T:
  candidates  my negotiations that ended without a receipt, last message in [T - WINDOW_S, T + 2], not linked yet
  score       the review names the SKU (+4), one of the shop's asks (+3), the item name's first 12 characters (+2),
              the category leaf (+1); then the nearest end time
  linked      the best candidate, when its score is above every other candidate's or it is the only one
  a candidate with no text evidence (score 0) is linked only when it is the only one and ended within TIGHT_S
Reasons: EXTRA (price phrases qc.REASONS misses, "none" = no reason given), then depts/qc.REASONS (price / stock /
delay), else "other"; an empty review is "no text".
Prices are not touched; nothing here changes the shop's behaviour.
"""
import json
import os
import re
import sqlite3
from collections import Counter

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW_S = 60
TIGHT_S = 10
# reasons the qc.REASONS patterns miss (11/08 review: "lowest ask ... stayed above what I can pay", "mandate guard's
# limit"); used here only, before qc.REASONS. "none": the buyer gives no reason ("No deal for G...; no completed purchase.")
EXTRA = [("price", r"stayed above|can pay|can't pay|cannot pay|afford|guard's limit|price limit|over my|above my|lowest ask|cheaper"),
         ("none", r"^no deal for \S+; no completed purchase\.?$")]
SOFA_CAT = "소파/의자/스툴"


def _catalog():
    with open(os.path.join(HERE, ".market", "catalog.json"), encoding="utf-8") as f:
        return json.load(f)


def link(db, t0, t1):
    """[(review row, deal dict or None)] for the no-receipt results in [t0, t1)."""
    cat = _catalog()
    deals = {}
    for task, sku, cost, outcome in db.execute(
            "SELECT task, sku, cost, outcome FROM shop_deals WHERE ts>=? AND ts<? AND outcome IN ('cancelled','rejected')",
            (t0 - 3600, t1)):
        deals[task] = {"task": task, "sku": sku, "cost": cost or 0, "outcome": outcome, "asks": set(), "end": 0}
    for task, ts, who, text in db.execute("SELECT task, ts, who, text FROM shop_msgs WHERE ts>=? AND ts<?",
                                          (t0 - 3600, t1 + 60)):
        d = deals.get(task)
        if d is None:
            continue
        d["end"] = max(d["end"], ts)
        m = re.match(r"(?:ask|counter) (\d+)", text or "") if who == "shop" else None
        if m:
            d["asks"].add(m.group(1))
    by_end = sorted(deals.values(), key=lambda d: d["end"])
    used, out = set(), []
    rows = db.execute("SELECT ts, buyer, stars, review FROM my_results WHERE has_receipt=0 AND ts>=? AND ts<? ORDER BY ts",
                      (t0, t1)).fetchall()
    for ts, buyer, stars, review in rows:
        r = review or ""
        nums = set(re.findall(r"\d{3,}", r.replace(",", "")))
        cands = []
        for d in by_end:
            if d["end"] < ts - WINDOW_S:
                continue
            if d["end"] > ts + 2:
                break
            if d["task"] in used:
                continue
            c = cat.get(d["sku"]) or {}
            s = (4 * (d["sku"] in r) + 3 * bool(nums & d["asks"]) + 2 * bool(c.get("name") and c["name"][:12] in r)
                 + 1 * bool((c.get("category") or "").split(">")[-1] and (c.get("category") or "").split(">")[-1] in r))
            cands.append((s, -abs(ts - d["end"]), d))
        cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
        best = None
        if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]):
            # no text evidence (score 0): only a lone candidate that ended within TIGHT_S of the result
            if cands[0][0] > 0 or (len(cands) == 1 and -cands[0][1] <= TIGHT_S):
                best = cands[0][2]
                used.add(best["task"])
        out.append(({"ts": ts, "buyer": buyer, "stars": stars, "review": r}, best))
    return out


def reason(text):
    from depts.qc import REASONS
    t = (text or "").strip()
    if not t:
        return "no text"
    order = ([x for x in EXTRA if x[0] == "none"] + [x for x in REASONS if x[0] == "stock"]   # "not in stock" before
             + [x for x in EXTRA if x[0] != "none"] + REASONS)                              # "no price in ..."
    return next((k for k, p in order if re.search(p, t, re.I)), "other")


def high_sofa_reasons(t0, t1, db=None):
    """Reason counts for my cancelled / rejected high-price (cost > qc.HIGH_MIN) and sofa negotiations in [t0, t1),
    with how many of all no-receipt results could be linked."""
    from depts.qc import HIGH_MIN
    own = db is None
    db = db or sqlite3.connect(os.path.join(HERE, "market.db"), timeout=30)
    try:
        pairs = link(db, t0, t1)
        n_deals = {g: db.execute(
            "SELECT COUNT(*) FROM shop_deals WHERE ts>=? AND ts<? AND outcome IN ('cancelled','rejected') AND " + w,
            (t0, t1)).fetchone()[0] for g, w in (("high", f"cost>{HIGH_MIN}"),)}
    finally:
        if own:
            db.close()
    cat = _catalog()
    sofas = {k for k, c in cat.items() if (c.get("category") or "").split(">")[-1] == SOFA_CAT}
    res = {"results": len(pairs), "linked": sum(1 for _, d in pairs if d), "high": Counter(), "sofa": Counter(),
           "high_deals": n_deals["high"]}
    sofa_n = 0
    for rv, d in pairs:
        if not d:
            continue
        if d["sku"] in sofas:
            res["sofa"][reason(rv["review"])] += 1
        elif d["cost"] > HIGH_MIN:
            res["high"][reason(rv["review"])] += 1
    return res


def line(res):
    f = lambda c: (", ".join(f"{k} {v} ({v / sum(c.values()):.0%})" for k, v in c.most_common()) if c else "연결된 리뷰 없음")
    return (f"고가·소파 취소 협상 사유 (리뷰를 시각으로 연결: 무영수증 리뷰 {res['results']}건 중 {res['linked']}건 연결)\n"
            f"   고가 {sum(res['high'].values())}건: {f(res['high'])}\n   소파 {sum(res['sofa'].values())}건: {f(res['sofa'])}")
