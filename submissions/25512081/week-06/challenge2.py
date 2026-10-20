"""Challenge 2: the shopper against 20 shops, judged from the shops' own logs.

    python challenge2.py            # -> logs/challenge-2/{shop-NN.log, shopper.log, summary.json}

Starts seller.py 20 times (shops/shop-NN.json), writes the directory of their
URLs, and runs shopper.py with shopping_list.json and the budget below. The
shopper sees only the cards and the list's words; the spec of each list item
stays here, in the judge.

It passes when, counting the [seller] DEAL lines in the shop logs (not the
shopper's own report): every list item has exactly one deal, on an item that
fits its spec, and all deals together cost no more than the budget.
"""
import json
import re
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

OUT = Path("logs/challenge-2")
WOOD = {"oak", "pine", "walnut", "teak", "birch", "bamboo", "beech"}
SPEC = {
    "L1": lambda i: i["category"] == "desk lamp" and i["dimmable"],
    "L2": lambda i: i["category"] == "bookshelf" and i["material"] in WOOD and i["capacity_books"] >= 40,
    "L3": lambda i: i["category"] == "office chair" and i["armrests"],
    "L4": lambda i: i["category"] == "rug" and i["washable"],
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    shop_files = sorted(Path("shops").glob("shop-*.json"))
    shops = {f.stem: json.load(open(f)) for f in shop_files}
    wants = json.load(open("shopping_list.json"))

    # budget: tight enough that shop choice and haggling matter
    floor = mid = 0
    for w in wants:
        rs = sorted(it["reserve"] for s in shops.values() for it in s["items"] if SPEC[w["id"]](it))
        floor += rs[0]
        mid += statistics.median(rs)
    budget = round(mid * 1.1)
    print(f"budget {budget} (sum of cheapest fitting reserves {floor}, of median {mid:.0f})")

    procs, urls = [], []
    for f in shop_files:
        port = free_port()
        procs.append(subprocess.Popen(
            [sys.executable, "seller.py", "--port", str(port), "--catalog", str(f)],
            stdout=open(OUT / f"{f.stem}.log", "w"), stderr=subprocess.STDOUT))
        urls.append(f"http://127.0.0.1:{port}")
    try:
        for u in urls:
            for _ in range(60):
                try:
                    httpx.get(u + "/.well-known/agent-card.json", timeout=2)
                    break
                except httpx.HTTPError:
                    time.sleep(0.5)
        json.dump({"shops": urls}, open(OUT / "directory.json", "w"), indent=1)
        t0 = time.time()
        subprocess.run([sys.executable, "shopper.py", "--directory", str(OUT / "directory.json"),
                        "--list", "shopping_list.json", "--budget", str(budget),
                        "--out", str(OUT / "result.json")],
                       stdout=open(OUT / "shopper.log", "w"), stderr=subprocess.STDOUT)
        secs = time.time() - t0
    finally:
        for p in procs:
            p.terminate()
        time.sleep(1)

    # ---- judge from the shops' logs
    deals = []
    for f in shop_files:
        for line in open(OUT / f"{f.stem}.log"):
            m = re.search(r"DEAL (\S+) at (\d+)", line)
            if m:
                item = next(it for it in shops[f.stem]["items"] if it["id"] == m.group(1))
                deals.append({"shop": shops[f.stem]["name"], "item": item["id"], "name": item["name"],
                              "price": int(m.group(2)), "reserve": item["reserve"], "_it": item})
    per_want = {w["id"]: [d for d in deals if SPEC[w["id"]](d["_it"])] for w in wants}
    total = sum(d["price"] for d in deals)
    checks = {
        "every item bought": all(len(v) >= 1 for v in per_want.values()),
        "no item bought twice": all(len(v) <= 1 for v in per_want.values())
                                and len(deals) == len(wants),
        "within budget": total <= budget,
    }
    for d in deals:
        d.pop("_it")
    summary = {"pass": all(checks.values()), "checks": checks, "budget": budget,
               "floor_cheapest_reserves": floor, "median_reserves": mid, "total": total,
               "deals": deals, "per_item": {k: [d["item"] for d in v] for k, v in per_want.items()},
               "seconds": round(secs)}
    json.dump(summary, open(OUT / "summary.json", "w"), indent=2)
    print(json.dumps({k: summary[k] for k in ("pass", "checks", "budget", "total")}, indent=1))
    for d in deals:
        print(f"  {d['shop']:18} {d['item']} {d['name']:32} {d['price']} (reserve {d['reserve']})")


if __name__ == "__main__":
    main()
