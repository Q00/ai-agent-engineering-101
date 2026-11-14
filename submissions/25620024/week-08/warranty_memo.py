"""Week 08 — warranty_memo.py: per-buyer warranty (MST TF 11-14, HQ "Yes, adopt it").

The shop never sees who is calling, so a buyer is known by the wording of its first message (shop_r3.fingerprint).
Each paid deal of ours writes {fp, unit} to private/fp_units.jsonl; the unit's settle on the public ledger names the
buyer. Then, from the ledger mirror (settles + settle_warranty, 48 h, goods only), each buyer's share of goods it paid
for WITHOUT a warranty, per grade, across every shop.

    private/warranty_memo.json   {"ts", "fp": {fp: {"buyer", "A": {"n", "no_w"}, "B": ..., "C": ...}}, "buyers": n}

The shop drops the warranty for a wording whose buyer paid without one 90%+ of the time (n >= 10) and says so in the
offer. Run from restock.py every warranty_memo_s seconds; plain SQL, well under a second.
"""
import collections
import json
import time
from pathlib import Path

import market_model

HERE = Path(__file__).parent
FP_LOG = HERE / "private" / "fp_units.jsonl"
OUT = HERE / "private" / "warranty_memo.json"


def build(c=None, now=None, hours=48, fp_hours=72):
    c, now = c or market_model.db(), now or time.time()
    rows = []
    try:
        for line in FP_LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("fp") and r.get("unit") and (r.get("ts") or 0) > now - fp_hours * 3600:
                rows.append(r)
    except OSError:
        pass
    units = {r["unit"] for r in rows}
    who = {}
    for i in range(0, len(units), 500):
        part = list(units)[i:i + 500]
        q = ",".join("?" * len(part))
        who.update(dict(c.execute(f"select x.unit, s.buyer from settle_units x join settles s on s.seq = x.seq "
                                  f"where x.unit in ({q})", part).fetchall()))
    votes = collections.defaultdict(collections.Counter)
    for r in rows:
        if who.get(r["unit"]):
            votes[r["fp"]][who[r["unit"]]] += 1
    fp_buyer = {}
    for fp, cnt in votes.items():                     # a wording belongs to one buyer: 2+ deals, 80%+ of them
        b, n = cnt.most_common(1)[0]
        if n >= 2 and n / sum(cnt.values()) >= 0.8:
            fp_buyer[fp] = b
    cat = market_model.catalog()
    stats = {}
    for b in set(fp_buyer.values()):
        g = collections.defaultdict(lambda: [0, 0])
        for sku, grade, hrs in c.execute("""select s.sku, s.grade, w.hours from settles s join settle_warranty w
                                            on w.seq = s.seq where s.buyer = ? and s.ts > ?""", (b, now - hours * 3600)):
            if (cat.get(sku) or {}).get("kind") != "goods":
                continue
            g[grade or "?"][0] += 1
            g[grade or "?"][1] += 0 if hrs else 1
        stats[b] = {k: {"n": v[0], "no_w": round(v[1] / v[0], 3)} for k, v in g.items() if v[0]}
    out = {"ts": now, "buyers": len(stats), "fp": {fp: {"buyer": b, **stats.get(b, {})} for fp, b in fp_buyer.items()}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = build()
    print(json.dumps({"buyers": o["buyers"], "wordings": len(o["fp"]),
                      "no_warranty_payers": sorted({v["buyer"] for v in o["fp"].values()
                                                    if any(isinstance(x, dict) and x["n"] >= 10 and x["no_w"] >= 0.9
                                                           for x in v.values())})}, ensure_ascii=False))
