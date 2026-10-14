"""Challenge 2: start the 20 shops of shops/, write directory.json, run shop_buyer.py on the list.

    python challenge2_run.py [--budget 800]     # logs in logs/challenge-2/

After the buyer exits, the checker counts the deals in the SHOPS' logs (not the buyer's word):
every list entry bought, no entry bought twice, total within the budget.
"""
import argparse
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-2"   # --out changes it


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=800)
    ap.add_argument("--out", default="logs/challenge-2")
    args = ap.parse_args()
    global OUT
    OUT = HERE / args.out
    OUT.mkdir(parents=True, exist_ok=True)
    configs = sorted(glob.glob(str(HERE / "shops" / "shop-*.json")))
    procs, urls = [], []
    for c in configs:
        cfg = json.load(open(c, encoding="utf-8"))
        name = Path(c).stem
        f = open(OUT / f"{name}.log", "w", encoding="utf-8")
        procs.append((subprocess.Popen([sys.executable, "seller.py", "--port", str(cfg["port"]), "--config", f"shops/{name}.json"],
                                       cwd=HERE, stdout=f, stderr=subprocess.STDOUT), f))
        urls.append(f"http://127.0.0.1:{cfg['port']}")
    try:
        for u in urls:
            for _ in range(60):
                try:
                    if httpx.get(u + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(1)
        (HERE / "directory.json").write_text(json.dumps(urls, indent=1) + "\n", encoding="utf-8")
        with open(OUT / "buyer.log", "w", encoding="utf-8") as f:
            subprocess.run([sys.executable, "shop_buyer.py", "--directory", "directory.json",
                            "--list", "shopping_list.json", "--budget", str(args.budget)],
                           cwd=HERE, stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    finally:
        for p, f in procs:
            p.terminate()
            p.wait(timeout=10)
            f.close()

    wanted = json.load(open(HERE / "shopping_list.json", encoding="utf-8"))
    deals = []
    for log in sorted(OUT.glob("shop-*.log")):
        for line in log.read_text(encoding="utf-8").splitlines():
            m = re.search(r"\[(\w+)\] -> completed, receipt (\{.*\})", line)
            if m:
                deals.append((log.stem, m.group(1), json.loads(m.group(2))))
    buyer_log = (OUT / "buyer.log").read_text(encoding="utf-8")
    bought = re.findall(r"\[entry(\d+)\] BOUGHT at (S\d+) for (\S+):", buyer_log)
    total = sum(d[2]["price"] for d in deals)
    lines = [f"# challenge 2 check, from the shops' logs: {len(deals)} deals"]
    lines += [f"  {shop} task {tid}: {r['name']} for {r['price']}" for shop, tid, r in deals]
    entries = [int(n) for n, _, _ in bought]
    ok_all = sorted(set(entries)) == list(range(1, len(wanted) + 1))
    ok_once = len(entries) == len(set(entries)) and len(deals) == len(entries)
    ok_budget = total <= args.budget
    lines += [f"every entry bought: {ok_all} ({sorted(entries)} of {len(wanted)})",
              f"no entry bought twice: {ok_once} (shop deals {len(deals)}, buyer entries {len(entries)})",
              f"total within budget: {ok_budget} ({total} <= {args.budget})",
              f"CHALLENGE 2: {'PASS' if ok_all and ok_once and ok_budget else 'FAIL'}"]
    (OUT / "check.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
