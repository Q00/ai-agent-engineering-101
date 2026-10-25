"""Challenge 2 driver: twenty shops, one shopping list, one budget. Writes logs/challenge-2/.

    python challenge2.py

Starts one seller.py per catalog in shops/ (ports 9201..9220), writes the directory, runs shop.py,
then checks the result against the shops themselves: every purchase is looked up with GetTask on
the shop that sold it (state COMPLETED, receipt price equal to what the buyer recorded). Passes when
every list item is bought, the total is within the budget, and no item is bought twice.
"""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "challenge-2"
CAP_SCALE = 1.0     # python challenge2.py --cap-scale 0.6 --out logs/challenge-2/retry-check
BASE_PORT = 9201
BUDGET = 1000
SHOPPING_LIST = [
    "a desk lamp for reading at night",
    "an office chair on wheels",
    "a wooden bookshelf that holds about 100 books",
    "a coffee table for a small living room",
]


def wait_card(url, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(url + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def get_task(url, task_id):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": "GetTask", "params": {"id": task_id}}
    return httpx.post(url + "/", json=body, headers={"A2A-Version": "1.0"}, timeout=30).json().get("result", {})


def main():
    global OUT, CAP_SCALE
    if "--out" in sys.argv:
        OUT = HERE / sys.argv[sys.argv.index("--out") + 1]
    if "--cap-scale" in sys.argv:
        CAP_SCALE = float(sys.argv[sys.argv.index("--cap-scale") + 1])
    OUT.mkdir(parents=True, exist_ok=True)
    catalogs = sorted((HERE / "shops").glob("shop-*.json"))
    procs, urls = [], []
    for n, cat in enumerate(catalogs):
        port = BASE_PORT + n
        log = open(OUT / f"seller-{cat.stem}.log", "w")
        procs.append(subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--catalog", str(cat)],
                                      cwd=HERE, stdout=log, stderr=subprocess.STDOUT))
        urls.append(f"http://127.0.0.1:{port}")
    try:
        ready = [u for u in urls if wait_card(u)]
        print(f"{len(ready)}/{len(urls)} shops up")
        (OUT / "directory.json").write_text(json.dumps(ready, indent=1) + "\n")
        (OUT / "list.json").write_text(json.dumps(SHOPPING_LIST, indent=1) + "\n")
        with open(OUT / "buyer.log", "w") as log:
            subprocess.run([sys.executable, "shop.py", "--directory", str(OUT / "directory.json"),
                            "--list", str(OUT / "list.json"), "--budget", str(BUDGET), "--out", str(OUT / "result.json"),
                            "--cap-scale", str(CAP_SCALE)],
                           cwd=HERE, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        result = json.loads((OUT / "result.json").read_text())

        checks, total = [], 0
        for p in result["purchases"]:
            t = get_task(p["url"], p["task_id"])
            arts = [part.get("data", {}) for a in t.get("artifacts", []) for part in a.get("parts", [])]
            price = next((d.get("price") for d in arts if isinstance(d, dict) and "price" in d), None)
            ok = t.get("status", {}).get("state") == "TASK_STATE_COMPLETED" and price == p["price"]
            checks.append({"item": p["item"], "shop": p["shop_name"], "task": p["task_id"], "seller_item": p["seller_item"],
                           "recorded_price": p["price"], "shop_says": price, "state": t.get("status", {}).get("state"), "ok": ok})
            total += price or 0
        items_bought = [c["item"] for c in checks]
        verdict = {
            "every_item_bought": sorted(items_bought) == sorted(SHOPPING_LIST),
            "within_budget": total <= BUDGET,
            "no_item_twice": len(items_bought) == len(set(items_bought)),
            "receipts_confirmed_by_shops": all(c["ok"] for c in checks),
            "total": total, "budget": BUDGET, "purchases": checks,
            "seconds": result["seconds"], "harness_calls": result["harness_calls"],
        }
        verdict["passed"] = all(verdict[k] for k in ("every_item_bought", "within_budget", "no_item_twice",
                                                     "receipts_confirmed_by_shops"))
        (OUT / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n")
        print(json.dumps({k: v for k, v in verdict.items() if k != "purchases"}))
        for c in checks:
            print(f"  {c['item']!r} <- {c['shop']}: {c['seller_item']!r} at {c['shop_says']} ({c['state']})")
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)


if __name__ == "__main__":
    main()
