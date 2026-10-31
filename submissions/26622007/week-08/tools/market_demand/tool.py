import json
import os
import sys

import market_model as mm

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
catalog = json.load(open(os.path.join(md, "catalog.json"), encoding="utf-8"))
me = {os.environ.get("SHOP_MARKET_NAME", "nocha12")}
m = mm.estimate(mm.load_rows(md), catalog, me, mm.my_shops(), cfg=None)
want = args.get("category") or ""
rows = sorted(((k, v) for k, v in m["skus"].items() if want in catalog[k]["category"]), key=lambda kv: -kv[1]["rate"])
out = {"reachable_deals": m["n"], "active": m["active"], "captive_buyers": len(m["captive"]),
       "skus": [{"sku": k, "name": catalog[k]["name"], "category": catalog[k]["category"], "list_price": catalog[k]["list_price"],
                 "kind": catalog[k]["kind"], "shelf_hours": catalog[k].get("shelf_hours"), **v}
                for k, v in rows[:args.get("limit", 20)]],
       "categories": dict(sorted(m["cats"].items(), key=lambda kv: -kv[1]["rate"])[:args.get("limit", 20)])}
if args.get("buyer"):
    out["buyer_favourites"] = m["buyers"].get(args["buyer"], [])
print(json.dumps(out, ensure_ascii=False))
