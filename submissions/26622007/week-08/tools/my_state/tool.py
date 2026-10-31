import collections
import json
import os
import sys
import time

args, md = json.load(sys.stdin), os.environ["MARKET_DIR"]
inv = json.load(open(os.path.join(md, "inventory.json"), encoding="utf-8"))
now, units = time.time(), inv.get("units") or []
cats = collections.defaultdict(lambda: {"units": 0, "cost": 0})
for u in units:
    cats[u.get("category")]["units"] += 1
    cats[u.get("category")]["cost"] += u.get("cost") or 0
soon = [u for u in units if u.get("expires_at") and u["expires_at"] - now < 86400]
keep = ("unit", "sku", "name", "grade", "cost", "list_price", "expires_at", "supplied_at")
n = args.get("limit", 10)
print(json.dumps({k: inv.get(k) for k in ("round", "balance", "start", "injected", "bankruptcies_left", "bankrupt_line")}
                 | {"units": len(units), "stock_cost": round(sum(u.get("cost") or 0 for u in units)),
                    "by_category": dict(sorted(cats.items(), key=lambda kv: -kv[1]["cost"])[:n]),
                    "expiring_24h": [{k: u.get(k) for k in keep} for u in soon[:n]],
                    "oldest": [{k: u.get(k) for k in keep} for u in sorted(units, key=lambda u: u.get("supplied_at") or 0)[:n]]},
                 ensure_ascii=False))
