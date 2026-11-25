import json
import os
import sys

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
catalog = json.load(open(os.path.join(md, "catalog.json"), encoding="utf-8"))
q, out = args.get("query") or "", []
for sku, x in catalog.items():
    if (q in sku or q in x.get("name", "") or q in x.get("category", "")) and args.get("kind") in (None, "", x.get("kind")) \
            and x.get("list_price", 0) <= args.get("max_price", float("inf")) \
            and (x.get("shelf_hours") or float("inf")) >= args.get("min_shelf_hours", 0):
        out.append({"sku": sku, **x})
        if len(out) >= args.get("limit", 20):
            break
print(json.dumps(out, ensure_ascii=False))
