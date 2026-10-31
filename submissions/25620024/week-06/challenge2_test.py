"""Week 06 challenge 2 — challenge2_test.py: part F, the buyer against 20 shops, graded from the shops' side.

    python challenge2_test.py                  # every scenario below
    python challenge2_test.py tight            # only scenarios whose name starts with this

For each scenario: start the 20 shops of shops.json (fresh, so no state carries over), run
challenge2_buyer.py with the scenario's budget, stop the shops, then grade from the SHOPS' logs,
not from what the buyer says about itself:
  every_item   every item on the list has a deal at some shop
  budget       the deals add up to at most the budget
  no_twice     no item has more than one deal
A deal is the shop's "DEAL <id> at <price>" line; it is matched to a list item through the
first request the shop logged for that task.
Writes logs/challenge-2/<scenario>/ (buyer.log, shops/), challenge2-test.log, challenge2-report.json.
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import start_shops

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "challenge-2")
SCENARIOS = [
    {"name": "normal", "budget": 800, "note": "about 1.2 x the sum of typical reserves"},
    {"name": "tight", "budget": 700, "note": "just above the sum of the cheapest shops' reserves (about 660)"},
]
FIRST_PORT = 9501
BUYER_TIMEOUT = 1200   # seconds


def deals_from_shop_logs(shop_dir):
    """Returns [{"shop", "task", "request", "item_id", "price"}] from every shop log."""
    deals = []
    for fn in sorted(os.listdir(shop_dir)):
        lines = open(os.path.join(shop_dir, fn), encoding="utf-8").read().splitlines()
        shop = lines[0].split(" on http")[0] if lines else fn
        first_request = {}
        for ln in lines:
            m = re.match(r"\[(\w+)\] request: (['\"])(.*)\2$", ln)
            if m and m.group(1) not in first_request:
                first_request[m.group(1)] = m.group(3)
            m = re.match(r"\[(\w+)\] DEAL (\S+) at (\d+)", ln)
            if m:
                deals.append({"shop": shop, "task": m.group(1), "request": first_request.get(m.group(1), ""),
                              "item_id": m.group(2), "price": int(m.group(3))})
    return deals


def run_scenario(sc, items):
    out = os.path.join(OUT, sc["name"])
    shop_dir = os.path.join(out, "shops")
    os.makedirs(shop_dir, exist_ok=True)
    print(f"\n=== scenario {sc['name']}: budget {sc['budget']} ({sc['note']}) ===", flush=True)
    procs, urls = start_shops.start(first_port=FIRST_PORT, out_dir=shop_dir)
    directory = os.path.join(out, "directory.json")
    json.dump(urls, open(directory, "w"), indent=2)
    t0 = time.time()
    lines = []
    buyer = None
    try:
        buyer = subprocess.Popen([sys.executable, "challenge2_buyer.py", "--budget", str(sc["budget"]),
                                  "--directory", directory], cwd=HERE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
        for line in buyer.stdout:                     # show the buyer live; keep it in memory
            print("  " + line, end="", flush=True)
            lines.append(line)
        buyer.wait(timeout=BUYER_TIMEOUT)
    finally:
        if buyer and buyer.poll() is None:
            buyer.kill()
        start_shops.stop(procs)
        time.sleep(1)
        # v2: written once at the end, with retries -- a live write on Google Drive timed out
        # (Errno 60) mid-run and left buyer.log empty (devlogs/challenge2-test-v2-credit-out-1018.txt)
        for attempt in range(5):
            try:
                with open(os.path.join(out, "buyer.log"), "w", encoding="utf-8") as blog:
                    blog.write("".join(lines))
                break
            except OSError as e:
                print(f"  (writing buyer.log failed: {e}; retrying)", flush=True)
                time.sleep(3)
    seconds = round(time.time() - t0)

    deals = deals_from_shop_logs(shop_dir)
    per_item = {it: [d for d in deals if d["request"].startswith(it)] for it in items}
    stray = [d for d in deals if not any(d["request"].startswith(it) for it in items)]
    total = sum(d["price"] for d in deals)
    checks = {"every_item": all(per_item[it] for it in items),
              "budget": total <= sc["budget"],
              "no_twice": all(len(v) <= 1 for v in per_item.values()) and not stray}
    buyer_text = "".join(lines)
    guards = re.findall(r"GUARD .*", buyer_text)
    sizes = [int(s) for s in re.findall(r"prompt (\d+) chars", buyer_text)]
    result = {"scenario": sc["name"], "budget": sc["budget"], "pass": all(checks.values()), "checks": checks,
              "total": total, "seconds": seconds,
              "deals": {it: [f"{d['item_id']} at {d['price']} from {d['shop']}" for d in v] for it, v in per_item.items()},
              "stray_deals": stray, "buyer_guards": guards,
              "buyer_harness_turns": len(sizes), "prompt_chars": [min(sizes), max(sizes)] if sizes else []}
    verdict = (f"{'PASS' if result['pass'] else 'FAIL'}  {sc['name']}: total {total} of {sc['budget']}, "
               f"{sum(1 for v in per_item.values() if v)}/{len(items)} items, {checks}, "
               f"{len(guards)} buyer guard(s), prompt {result['prompt_chars']} chars, {seconds} s")
    print(verdict, flush=True)
    for it, v in result["deals"].items():
        print(f"    {it[:50]:50} {v or 'NOT BOUGHT'}", flush=True)
    return result, verdict


def main():
    items = json.load(open(os.path.join(HERE, "shopping_list.json")))["items"]
    scenarios = [s for s in SCENARIOS if not sys.argv[1:] or any(s["name"].startswith(p) for p in sys.argv[1:])]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results, lines = [], [f"# challenge 2 test, {started}, {len(items)} items, shops from shops.json"]
    for sc in scenarios:
        r, verdict = run_scenario(sc, items)
        results.append(r)
        lines.append(verdict)
    passed = sum(r["pass"] for r in results)
    lines.append(f"\n{passed}/{len(results)} scenarios passed")
    print(lines[-1])
    with open(os.path.join(OUT, "challenge2-test.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(OUT, "challenge2-report.json"), "w", encoding="utf-8") as f:
        json.dump({"started": started, "items": items, "passed": passed, "total": len(results), "results": results},
                  f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
