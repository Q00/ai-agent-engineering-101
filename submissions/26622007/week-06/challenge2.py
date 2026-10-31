"""Challenge 2: runs 20 copies of seller.py (catalogs/shops.json) and the shopper against them.

    python challenge2.py --budget 2000

Passes when every item on shopping_list.json is bought, the total stays within the
budget, and no item is bought twice. The deals are counted twice: from the
shopper's result and, independently, from the COMPLETED lines in the 20 seller logs.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx


def wait_card(base, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if httpx.get(base + "/.well-known/agent-card.json", timeout=3).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=2000)
    ap.add_argument("--list", default="shopping_list.json")
    ap.add_argument("--shops", default="catalogs/shops.json")
    a = ap.parse_args()
    shops = json.loads(Path(a.shops).read_text())
    wanted = json.loads(Path(a.list).read_text())
    out = Path("logs/challenge-2") / f"run-{time.strftime('%m%d-%H%M')}"
    (out / "sellers").mkdir(parents=True, exist_ok=True)

    procs = []
    try:
        for s in shops:
            f = open(out / "sellers" / f"{s['id']}.log", "w")
            procs.append((subprocess.Popen([sys.executable, "seller.py", "--port", str(s["port"]), "--catalog",
                                            s["catalog"], "--name", s["name"]], stdout=f, stderr=subprocess.STDOUT), f))
        urls = [f"http://127.0.0.1:{s['port']}" for s in shops]
        if not all(wait_card(u) for u in urls):
            print("FAIL some shops did not start")
            return 1
        (out / "directory.json").write_text(json.dumps(urls, indent=1) + "\n")
        print(f"{len(urls)} shops up; shopping list {wanted}; budget {a.budget}", flush=True)
        with open(out / "shopper.log", "w") as f:
            rc = subprocess.run([sys.executable, "shopper.py", "--directory", str(out / "directory.json"),
                                 "--list", a.list, "--budget", str(a.budget), "--out", str(out / "result.json")],
                                stdout=f, stderr=subprocess.STDOUT, timeout=1800).returncode
    finally:
        for p, f in procs:
            p.terminate()
        for p, f in procs:
            p.wait(timeout=10)
            f.close()

    result = json.loads((out / "result.json").read_text()) if (out / "result.json").exists() else {"items": []}
    bought = [i for i in result["items"] if i["status"] == "bought"]
    total = sum(i["price"] for i in bought)
    seller_deals = []
    for s in shops:
        for line in (out / "sellers" / f"{s['id']}.log").read_text().splitlines():
            m = re.search(r"-> COMPLETED (\S+) at (\d+)", line)
            if m:
                seller_deals.append((s["id"], m.group(1), int(m.group(2))))
    checks = {
        "every item bought": len(bought) == len(wanted),
        f"total {total} within budget {a.budget}": total <= a.budget,
        f"no item bought twice (seller logs show {len(seller_deals)} deals for {len(wanted)} items)":
            len(seller_deals) == len(bought) and len({i["index"] for i in bought}) == len(bought),
    }
    lines = [f"# challenge 2, {out.name}: shopper exit {rc}, {result.get('seconds')} s"]
    for i in result["items"]:
        r = i.get("receipt") or {}
        lines.append(f"- #{i['index']} {i['want']}: {i['status']} {i['price']} (cap {i['cap']}) via {i['tries']} "
                     f"-> {r.get('seller')} / {r.get('item')}")
    lines.append(f"seller-side deals: {seller_deals}")
    for k, v in checks.items():
        lines.append(f"{'PASS' if v else 'FAIL'}  {k}")
    lines.append("PASS" if all(checks.values()) else "FAIL")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
