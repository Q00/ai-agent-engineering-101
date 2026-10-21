"""Challenge 2: my multi-shop buyer against 20 copies of my seller.

    python challenges/run_challenge2.py [--budget 750]     # from submissions/26620029/week-06

Starts seller.py once per challenges/shops/S*.json (own catalog subset and prices),
runs buyer_multi.py with the directory, the list in ch2_list.py and one budget,
then checks the buyer's ledger against what the SHOPS logged ([deal] lines):
  - every list item bought, and the bought sku passes that item's check;
  - total within the budget;
  - no item bought twice, and no shop sold anything the ledger does not show.
Writes logs/challenge-2/attempt-N/{buyer.log, shops/S01.log ..., ledger.json, check.log}.
"""
import argparse
import glob
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
SUB = HERE.parent
sys.path[:0] = [str(SUB), str(HERE)]
import catalog                          # noqa: E402
from ch2_list import LIST               # noqa: E402

BUYER_TIMEOUT = 1200


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=750)
    args = ap.parse_args()
    base = SUB / "logs" / "challenge-2"
    n = 1 + max([int(p.name.split("-")[1]) for p in base.glob("attempt-*")] or [0])
    out = base / f"attempt-{n}"
    (out / "shops").mkdir(parents=True)
    cfgs = [json.load(open(p)) for p in sorted(glob.glob(str(HERE / "shops" / "S*.json")))]
    procs, urls, files = [], [], []
    try:
        for cfg in cfgs:
            port = free_port()
            f = open(out / "shops" / f"{cfg['id']}.log", "w")
            files.append(f)
            procs.append(subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--shop",
                                           str(HERE / "shops" / f"{cfg['id']}.json")], cwd=SUB, stdout=f,
                                          stderr=subprocess.STDOUT))
            urls.append(f"http://127.0.0.1:{port}")
        for u in urls:
            for _ in range(60):
                try:
                    if httpx.get(u + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.5)
        (out / "dir.json").write_text(json.dumps({"shops": urls}, indent=1) + "\n")
        (out / "list.json").write_text(json.dumps([t for t, _ in LIST], indent=1) + "\n")
        with open(out / "buyer.log", "w") as bl:
            t0 = time.time()
            rc = subprocess.run([sys.executable, "buyer_multi.py", "--directory", str(out / "dir.json"),
                                 "--list", str(out / "list.json"), "--budget", str(args.budget),
                                 "--out", str(out / "ledger.json")], cwd=SUB, stdout=bl, stderr=subprocess.STDOUT,
                                timeout=BUYER_TIMEOUT).returncode
            secs = round(time.time() - t0)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
        for f in files:
            f.close()

    # ---- checks, from the shops' side as well as the buyer's
    lines, ok = [], True

    def check(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f"{'PASS' if cond else 'FAIL'}  {msg}")

    ledger = json.loads((out / "ledger.json").read_text()) if (out / "ledger.json").exists() else {"bought": {}}
    shop_items = {c["name"]: {it["sku"]: it for it in catalog.load_shop(c)} for c in cfgs}
    deals = []   # (shop, sku, price, task) from the shops' own logs
    for c in cfgs:
        for line in open(out / "shops" / f"{c['id']}.log"):
            m = re.match(r"\[deal\] (.+?) \| (\S+) \| ([\d.]+) \| task (\S+)", line)
            if m:
                deals.append((m.group(1), m.group(2), float(m.group(3)), m.group(4)))
    bought = ledger.get("bought", {})
    for text, chk in LIST:
        b = bought.get(text)
        it = b and shop_items.get(b["shop"], {}).get(b["sku"])
        check(b and it and chk(it), f"{text!r}: " + (f"{b['product']} ({b['sku']}) at {b['shop']} for {b['price']}"
                                                     if b else "not bought"))
    spent = sum(b["price"] for b in bought.values())
    check(spent <= args.budget, f"total {spent} within budget {args.budget}")
    check(len(deals) == len(bought), f"shops logged {len(deals)} deals, ledger has {len(bought)} purchases")
    tasks = {b["task"] for b in bought.values()}
    check(all(d[3] in tasks for d in deals), "every shop-side deal is in the ledger (nothing bought twice or by accident)")
    lines.append(f"buyer exit {rc}, {secs}s, harness calls {ledger.get('harness_calls')}")
    lines.append(f"\n{'PASS' if ok else 'FAIL'}: challenge 2 attempt {n}")
    (out / "check.log").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
