import json
import os
import sys

import market_model as mm

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
try:
    catalog = json.load(open(os.path.join(md, "catalog.json"), encoding="utf-8"))
except OSError:
    catalog = {}
out = []
for r in reversed(mm.load_rows(md)):
    if r["k"] != "settle":
        continue
    x = catalog.get(r.get("sku")) or {}
    if all((not args.get(k) or r.get(k) == args[k]) for k in ("sku", "buyer", "shop")) \
            and (args.get("category") or "") in (x.get("category") or ""):
        out.append({**r, "name": x.get("name"), "list_price": x.get("list_price")})
        if len(out) >= args.get("limit", 20):
            break
print(json.dumps(out, ensure_ascii=False))
