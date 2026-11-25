import collections
import json
import os
import sys

import market_model

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
try:
    mine = set(json.load(open(os.path.join(md, "loss_state.json"), encoding="utf-8")).get("mine", {}))
except (OSError, ValueError):
    mine = set()
try:
    catalog = json.load(open(os.path.join(md, "catalog.json"), encoding="utf-8"))
except (OSError, ValueError):
    catalog = {}
rows = market_model.load_break_rows(md)       # the settle and broken rows it reads
broke = {s["seq"] for _, s in market_model.link_broken(rows)}
by = collections.defaultdict(lambda: {"deals": 0, "ratios": [], "skus": collections.Counter(), "broke": 0})
for r in rows:
    if r.get("k") != "settle" or str(r.get("seq")) not in mine:
        continue
    b = by[r.get("buyer")]
    b["deals"] += 1
    b["skus"][r.get("sku")] += 1
    b["broke"] += r["seq"] in broke
    price = (catalog.get(r.get("sku")) or {}).get("list_price")
    if price and r.get("amount"):
        b["ratios"].append(r["amount"] / price)
out = []
for buyer, b in by.items():
    if b["deals"] < args.get("min_deals", 1):
        continue
    rs = sorted(b["ratios"])
    out.append({"buyer": buyer, "deals": b["deals"], "median_share_of_list": round(rs[len(rs) // 2], 3) if rs else None,
                "lowest_share": round(rs[0], 3) if rs else None, "top_skus": b["skus"].most_common(3), "broke": b["broke"]})
out.sort(key=lambda x: -x["deals"])
print(json.dumps({"my_settles_known": len(mine), "customers": out[:args.get("limit", 15)]}, ensure_ascii=False))
