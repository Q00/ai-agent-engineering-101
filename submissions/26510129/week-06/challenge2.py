"""Challenge 2 driver: twenty of my shops, my shopper, a shopping list and one total budget.

    python challenge2.py      # writes logs/challenge-2/

Starts shops/shop-01..20.json as twenty seller processes and writes their URLs to logs/challenge-2/directory.json.
The shopper gets only the descriptions (logs/challenge-2/list.json); the specs stay with this driver. The verdict
comes from the shops' own DEAL lines, not from what the shopper reports:
  - every list item is bought exactly once, and the item fits that entry's spec;
  - there are no other deals;
  - all deals together stay within the budget, and none is below the item's reserve at that shop.
"""
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

import catalog
from spec_check import satisfies

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-2"
TIMEOUT = 1500
LOG = []


def log(line=""):
    print(line, flush=True)
    LOG.append(line)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    (OUT / "shops").mkdir(parents=True, exist_ok=True)
    spec = json.loads((HERE / "challenges" / "list-2.json").read_text(encoding="utf-8"))
    budget, wants = spec["budget"], spec["items"]
    (OUT / "list.json").write_text(json.dumps({"items": [{"id": w["id"], "want": w["want"]} for w in wants]}, indent=1) + "\n")
    cfgs = sorted((HERE / "shops").glob("shop-*.json"))
    procs, urls, shop_items = [], [], {}
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    log(f"# challenge 2, {started}: {len(cfgs)} shops, {len(wants)} items, budget {budget}")
    t0 = time.time()
    try:
        for p in cfgs:
            port = free_port()
            name = p.stem
            out = open(OUT / "shops" / f"{name}.log", "w")
            procs.append((subprocess.Popen([sys.executable, "seller.py", "--config", f"shops/{p.name}", "--port", str(port)],
                                           cwd=HERE, stdout=out, stderr=subprocess.STDOUT), out))
            urls.append(f"http://127.0.0.1:{port}")
            shop_items[name] = {it["id"]: it for it in catalog.load_shop(json.loads(p.read_text(encoding="utf-8")))}
        for u in urls:
            for _ in range(120):
                try:
                    if httpx.get(u + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                        break
                except Exception:
                    pass
                time.sleep(0.5)
        (OUT / "directory.json").write_text(json.dumps(urls, indent=1) + "\n")
        for w in wants:
            fit = {n: [it["reserve"] for it in its.values() if satisfies(it, w["spec"])[0]] for n, its in shop_items.items()}
            fit = {n: v for n, v in fit.items() if v}
            log(f"{w['id']} {w['want']!r}: fitting items in {len(fit)} shops, cheapest reserve per shop "
                + ", ".join(f"{n[-2:]} {min(v)}" for n, v in fit.items()))
        cmd = [sys.executable, "shopper.py", "--directory", str(OUT / "directory.json"), "--list", str(OUT / "list.json"),
               "--budget", str(budget)]
        log(f"shopper: python shopper.py --directory logs/challenge-2/directory.json --list logs/challenge-2/list.json --budget {budget}")
        with open(OUT / "shopper.log", "w") as so:
            sp = subprocess.Popen(cmd, cwd=HERE, stdout=so, stderr=subprocess.STDOUT)
            try:
                rc = sp.wait(timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                sp.kill()
                rc = "timeout"
        log(f"shopper exit {rc} after {round(time.time() - t0)} s")
    finally:
        for proc, out in procs:
            proc.terminate()
        for proc, out in procs:
            proc.wait(timeout=10)
            out.close()

    deals, guard_lines, sizes = [], 0, []
    for p in sorted((OUT / "shops").glob("shop-*.log")):
        text = p.read_text(encoding="utf-8")
        guard_lines += text.count("[guard]")
        sizes += [int(x) for x in re.findall(r"in=(\d+)ch", text)]
        for m in re.finditer(r"\[seller\] DEAL task=(\S+) item=(\S+) reserve=(\d+) price=(\d+)", text):
            deals.append({"shop": p.stem, "task": m[1], "item": m[2], "reserve": int(m[3]), "price": int(m[4])})
    shopper_log = (OUT / "shopper.log").read_text(encoding="utf-8")
    buyer_sizes = [int(x) for x in re.findall(r"in=(\d+)ch", shopper_log)]
    guard_lines += shopper_log.count("[guard]")
    log("")
    problems, matched = [], {}
    for d in deals:
        it = shop_items[d["shop"]][d["item"]]
        fits = [w["id"] for w in wants if satisfies(it, w["spec"])[0]]
        d["fits"] = fits
        log(f"DEAL {d['shop']} {d['item']} {it['name']!r} at {d['price']} (reserve {d['reserve']}), fits {fits or 'nothing'}")
        if d["price"] < d["reserve"]:
            problems.append(f"{d['item']} sold below its reserve")
        if not fits:
            problems.append(f"deal {d['item']} fits no list item")
        for f in fits:
            matched.setdefault(f, []).append(d)
    for w in wants:
        n = len(matched.get(w["id"], []))
        if n != 1:
            problems.append(f"{w['id']} bought {n} times")
    total = sum(d["price"] for d in deals)
    if total > budget:
        problems.append(f"total {total} above the budget {budget}")
    passed = not problems
    summary = {"started": started, "seconds": round(time.time() - t0), "budget": budget, "total": total,
               "deals": len(deals), "shopper_harness_calls": len(buyer_sizes),
               "shopper_largest_prompt": max(buyer_sizes or [0]), "shop_harness_calls": len(sizes),
               "shop_largest_prompt": max(sizes or [0]), "guard_lines": guard_lines, "problems": problems, "pass": passed}
    log(f"total {total} of budget {budget}; {len(deals)} deals for {len(wants)} items; "
        f"shopper {len(buyer_sizes)} harness calls (largest prompt {summary['shopper_largest_prompt']} chars), "
        f"shops {len(sizes)} calls; guard lines {guard_lines}")
    log(f"{'PASS' if passed else 'FAIL'}" + (f": {'; '.join(problems)}" if problems else ""))
    (OUT / "result.json").write_text(json.dumps({"summary": summary, "deals": deals}, indent=1) + "\n")
    (OUT / "run.log").write_text("\n".join(LOG) + "\n")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
