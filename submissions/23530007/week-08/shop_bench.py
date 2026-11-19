"""Shop-side peer benchmark (11-12, user: 'when there is something like this, pass it through the memory module so it
reaches the shop too').

The shop part that still moves is stars_rank (one mean per reviewing account; book 1st and revenue 2nd are fixed),
so the comparison is by ACCOUNT: the same buyer account rating us and the student shops above us on stars.

    .market/shop_bench.json  (run by watch_round.sh every tick; memory_curator.read_digest('shop') attaches it)
      stars_ladder      student shops around us on stars_rank (board)
      accounts          accounts that rated us and a better-starred student shop in WINDOW_H (paid reviews, >= 3 each):
                        their mean to us vs to those shops, their low-star reasons for us (review words)
      above_us          for each student shop above us on stars: goods grade mix, warranty share, median price/list,
                        food share, honest-claim share over WINDOW_H, vs ours
      buyers_at_our_shop  how the buyers that pay us least open (first number / list, numbers per talk) and the
                        price/list they end at (our seller's concession), from our seller's message log

    python shop_bench.py [--print]
"""
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "data" / "market_archive.db"
OUT = HERE / ".market" / "shop_bench.json"
WINDOW_H = 6
ME = "yms979"
REASONS = {"grade": r"grade\s*[BC]|등급", "shelf": r"shelf|유통|h left|hours? left", "price": r"price|expensive|비싸|% of list|정가",
           "warranty": r"warrant|보증", "broken": r"broke|파손|고장", "receipt": r"receipt|영수증|mismatch|names? G"}


def build():
    now = time.time()
    db = sqlite3.connect(f"file:{ARCHIVE}?mode=ro", uri=True, timeout=30)
    cat = json.loads((HERE / ".market" / "catalog.json").read_text(encoding="utf-8"))
    names = {}
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='join'"):
        x = json.loads(d).get("data") or {}
        if x.get("url") and x.get("name"):
            names[x["url"].rstrip("/")] = x["name"]
    import board_api
    b = board_api.fetch(timeout=20)
    stud = {(p.get("shop") or {}).get("name"): p["player"] for p in b["players"] if p.get("student")}
    shops = sorted((s for s in b["shops"] if s["name"] in stud and s.get("stars_rank") is not None),
                   key=lambda s: -s["stars_rank"])
    i = next(k for k, s in enumerate(shops) if s["name"] == ME)
    above = [s["name"] for s in shops[:i]][-6:]
    out = {"t": now, "window_h": WINDOW_H,
           "stars_ladder": [{"rank": k + 1, "shop": s["name"], "player": stud.get(s["name"]), "stars_rank": s["stars_rank"]}
                            for k, s in enumerate(shops) if abs(k - i) <= 4]}
    rev = defaultdict(lambda: defaultdict(list))
    low_reasons = defaultdict(Counter)
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='result' AND ts > ?", (now - WINDOW_H * 3600,)):
        x = json.loads(d).get("data") or {}
        if not x.get("receipt") or not x.get("verified") or x.get("stars") is None:
            continue
        sh = names.get(str(x.get("shop")).rstrip("/"))
        if sh == ME or sh in above:
            rev[x["buyer"]][sh].append(x["stars"])
            if sh == ME and x["stars"] <= 3:
                text = str(x.get("review") or "")
                for k, pat in REASONS.items():
                    if re.search(pat, text, re.I):
                        low_reasons[x["buyer"]][k] += 1
    acc = []
    for buyer, by in rev.items():
        mine = by.get(ME) or []
        if len(mine) < 3:
            continue
        others = [(sh, L) for sh, L in by.items() if sh != ME and len(L) >= 3]
        if not others:
            continue
        o_mean = statistics.mean([v for _, L in others for v in L])
        m_mean = statistics.mean(mine)
        if o_mean - m_mean >= 0.3:
            acc.append({"account": buyer, "to_us": round(m_mean, 2), "n_us": len(mine), "to_better_shops": round(o_mean, 2),
                        "shops": {sh: round(statistics.mean(L), 2) for sh, L in others},
                        "our_low_reasons": dict(low_reasons[buyer].most_common(4))})
    acc.sort(key=lambda a: -(a["to_better_shops"] - a["to_us"]) * min(a["n_us"], 30))
    out["accounts"] = acc[:10]
    # how the shops above us sell (and how we sell)
    sales = defaultdict(list)
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='settle' AND ts > ?", (now - WINDOW_H * 3600,)):
        x = json.loads(d).get("data") or {}
        sh = names.get(str(x.get("shop")).rstrip("/"))
        if (sh == ME or sh in above) and x.get("sku") in cat:
            sales[sh].append(x)

    def prof(L):
        g = [x for x in L if x.get("expires_at") is None]
        return {"sales_6h": len(L), "food_share": round(1 - len(g) / len(L), 2) if L else None,
                "goods_grades": dict(Counter(x.get("grade") for x in g)),
                "goods_warranty_share": round(sum(1 for x in g if (x.get("warranty_hours") or 0) > 0) / len(g), 2) if g else None,
                "goods_median_x": round(statistics.median([x["amount"] / cat[x["sku"]]["list_price"] for x in g]), 3) if g else None,
                "honest_claims": round(sum(1 for x in L if x.get("claim_honest")) / len(L), 2) if L else None}
    out["ours"] = prof(sales.get(ME) or [])
    out["above_us"] = {sh: dict(prof(sales[sh]), stars_rank=next(s["stars_rank"] for s in shops if s["name"] == sh))
                       for sh in above if sales.get(sh)}
    out["buyers_at_our_shop"] = openings(db, now, cat)
    return out


def openings(db, now, cat):
    """buyer -> (first number / list, numbers per talk, paid / list) at our shop, from the seller's message log."""
    tasks = {}
    for (d,) in db.execute("SELECT data FROM ledger WHERE kind='result' AND ts > ?", (now - WINDOW_H * 3600,)):
        x = json.loads(d).get("data") or {}
        rc = x.get("receipt") or {}
        if rc.get("task"):
            tasks[rc["task"]] = (x.get("buyer"), rc.get("item"), rc.get("price"))
    conv = defaultdict(list)
    try:
        with open(HERE / "seller_r3_log.jsonl", errors="ignore") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 120_000_000))
            f.readline()
            for line in f:
                if '"msg"' not in line:
                    continue
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if x.get("task") in tasks:
                    s = str(x.get("text") or "").replace(",", "").strip()
                    if s.isdigit():
                        conv[x["task"]].append(int(s))
    except OSError:
        return {}
    by = defaultdict(list)
    for t, nums in conv.items():
        buyer, item, price = tasks[t]
        lp = (cat.get(item) or {}).get("list_price")
        if lp and nums and price:
            by[buyer].append((nums[0] / lp, len(nums), price / lp))
    return {k: {"talks": len(L), "first_number_x": round(statistics.median([a for a, _, _ in L]), 3),
                "numbers_per_talk": round(statistics.mean([n for _, n, _ in L]), 1),
                "paid_x": round(statistics.median([p for _, _, p in L]), 3)}
            for k, L in sorted(by.items(), key=lambda kv: -len(kv[1])) if len(L) >= 3}


def main():
    out = build()
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    if "--print" in sys.argv:
        print(json.dumps(out, ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
