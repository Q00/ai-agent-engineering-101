import collections
import json
import os
import sys

args = json.load(sys.stdin)
try:
    trades = json.load(open(os.environ.get("BUYER_MEMORY") or "memory/market_memory.json", encoding="utf-8"))["trades"]
except (OSError, ValueError, KeyError):
    trades = []
shops = collections.defaultdict(lambda: {"visits": 0, "deals": 0, "unpaid": 0, "broke": 0, "paid_of_list": [], "last_problem": None})
for t in trades:
    if t.get("order") is None:       # Round 3 trades carry the order; earlier rounds sold other things
        continue
    s = shops[t["shop"]]
    s["visits"] += 1
    s["unpaid"] += t["result"] == "unpaid"
    s["broke"] += bool(t.get("broke"))
    if t["result"] == "deal":
        s["deals"] += 1
        if t.get("price") and t.get("list_price"):
            s["paid_of_list"].append(t["price"] / t["list_price"])
    s["last_problem"] = t.get("unpaid_reason") or (t.get("problems") or [None])[-1] or s["last_problem"]
for s in shops.values():
    ratios = s.pop("paid_of_list")
    s["mean_paid_of_list"] = round(sum(ratios) / len(ratios), 3) if ratios else None
if args.get("shop"):
    print(json.dumps(shops.get(args["shop"]), ensure_ascii=False))
else:
    print(json.dumps(dict(sorted(shops.items(), key=lambda kv: -kv[1]["visits"])[:args.get("limit", 20)]), ensure_ascii=False))
