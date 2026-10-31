import json
import os
import sys

import market_model as mm

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
catalog = json.load(open(os.path.join(md, "catalog.json"), encoding="utf-8"))
s = mm.break_stats(mm.load_rows(md), catalog)
n = args.get("limit", 10)
worst = lambda t: dict(sorted(t.items(), key=lambda kv: -(kv[1]["broke"] / max(1, kv[1]["sold"])))[:n])
out = {"goods_sold": s["sold"], "goods_broke": s["broke"], "rate": s["rate"], "by_grade": s["by_grade"], "worst_shops": worst(s["by_shop"]),
       "worst_skus": worst(s["by_sku"])}
if args.get("sku"):
    out["sku"] = s["by_sku"].get(args["sku"])
    out["chance"] = mm.break_chance(s, args["sku"])
if args.get("shop"):
    out["shop"] = s["by_shop"].get(args["shop"])
print(json.dumps(out, ensure_ascii=False))
