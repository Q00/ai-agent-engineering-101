"""Week 06 — challenges 1 and 2, logged under logs/challenge-1/ and logs/challenge-2/.

    python run_challenges.py 1      # large catalog: buyers describe the item, never name it
    python run_challenges.py 2      # many shops: a directory of 20 cards, a shopping list, one budget

Challenge 1 starts one full-catalog seller and sends descriptive requests through
buyer.py --request. It passes when the receipt's item is of the expected kind and
its price is at or above that item's catalog reserve.

Challenge 2 writes 20 shop configs (different stock and price factors), starts 20
sellers, writes directory.json with their base URLs, and runs buyer.py with the
shopping list. It passes when every item is bought, the total stays within the
budget, and no item is bought twice.
"""
import json
import os
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
PY = sys.executable
ENV = dict(os.environ, PYTHONUNBUFFERED="1")


def start(cmd, logfile):
    f = open(logfile, "w", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=HERE, stdout=f, stderr=subprocess.STDOUT, env=ENV, start_new_session=True), f


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def wait_card(base, seconds=60):
    for _ in range(seconds * 2):
        try:
            if httpx.get(base + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def run_buyer(args, logfile, timeout=600):
    with open(logfile, "w", encoding="utf-8") as f:
        p = subprocess.run([PY, "buyer.py", *args], cwd=HERE, stdout=f, stderr=subprocess.STDOUT, env=ENV, timeout=timeout)
    return p.returncode, Path(logfile).read_text(encoding="utf-8")


# ---------------------------------------------------------------- challenge 1

REQUESTS = [  # (description the buyer sends, expected kind, budget)
    ("something wooden to keep about 40 books, not taller than my desk", "bookshelf", 420),
    ("a small dimmable light for my bedside table", "lamp", 200),
    ("somewhere to sit at my desk for long hours", "chair", 260),
    ("a high seat for the kitchen counter, metal if possible", "stool", 150),
]


def challenge1():
    out = HERE / "logs" / "challenge-1"
    out.mkdir(parents=True, exist_ok=True)
    catalog = {i["name"]: i for i in json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))}
    port = 9100
    seller, f = start([PY, "seller.py", "--port", str(port)], out / "seller.log")
    results = []
    try:
        assert wait_card(f"http://127.0.0.1:{port}"), "seller did not start"
        for n, (req, kind, budget) in enumerate(REQUESTS, 1):
            rc, text = run_buyer(["--url", f"http://127.0.0.1:{port}", "--budget", str(budget), "--request", req], out / f"buyer-{n}.log")
            m = re.search(r"\[(?:item|\w+) @ [^\]]+\] (bought at (\d+)|walked|rejected|no_offer|no_ask|error)", text)
            # the seller log names the chosen item
            chosen = re.findall(r"item='([^']+)' \((.*?), reserve (\d+)\) opening ask (\d+)", (out / "seller.log").read_text(encoding="utf-8"))
            item_name, how, reserve, ask = chosen[-1] if len(chosen) >= n else ("?", "?", "0", "0")
            price = int(m.group(2)) if m and m.group(2) else None
            ok_kind = item_name.endswith(kind)
            ok_reserve = int(ask) >= int(reserve) and (price is None or price >= int(reserve))
            verdict = "PASS" if ok_kind and ok_reserve else "FAIL"
            line = (f"{verdict} request {n}: {req!r} -> seller chose {item_name!r} ({how}, reserve {reserve}, opening ask {ask}); "
                    f"buyer: {m.group(1) if m else 'unknown'}; expected kind {kind}; kind ok={ok_kind} reserve ok={ok_reserve}")
            print(line); results.append(line)
    finally:
        stop(seller); f.close()
    (out / "summary.txt").write_text("\n".join(results) + "\n", encoding="utf-8")
    return all(r.startswith("PASS") for r in results)


# ---------------------------------------------------------------- challenge 2

KINDS = ["lamp", "chair", "bookshelf", "desk", "table", "sofa", "cabinet", "stool", "mirror", "rug", "bed frame", "nightstand"]
SHOPPING_LIST = ["lamp", "chair", "bookshelf", "stool", "mirror"]
TOTAL_BUDGET = 900


def write_shops(n=20):
    random.seed(606)
    (HERE / "shops").mkdir(exist_ok=True)
    bases = []
    for k in range(1, n + 1):
        cats = random.sample(KINDS, random.choice([2, 3, 4]))
        if k <= len(SHOPPING_LIST):              # make sure every list item has at least one shop
            cats = sorted(set(cats) | {SHOPPING_LIST[k - 1]})
        cfg = {"name": f"Shop {k:02d} ({', '.join(cats)})", "categories": cats,
               "price_factor": round(random.uniform(0.8, 1.4), 2),
               "description": f"Sells {', '.join(cats)}. Negotiates the price.",
               "skill_description": f"Name or describe a {', '.join(cats[:-1])} or {cats[-1]}, then send a price."}
        (HERE / "shops" / f"shop_{k:02d}.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        bases.append(f"http://127.0.0.1:{9100 + k}")
    (HERE / "directory.json").write_text(json.dumps(bases, indent=1), encoding="utf-8")
    return bases


def challenge2():
    out = HERE / "logs" / "challenge-2"
    out.mkdir(parents=True, exist_ok=True)
    bases = write_shops()
    procs = []
    try:
        for k, base in enumerate(bases, 1):
            procs.append(start([PY, "seller.py", "--port", str(9100 + k), "--config", f"shops/shop_{k:02d}.json"], out / f"shop_{k:02d}.log"))
        up = [wait_card(b) for b in bases]
        print(f"{sum(up)}/{len(bases)} shops up")
        rc, text = run_buyer(["--directory", "directory.json", "--list", ",".join(SHOPPING_LIST), "--budget", str(TOTAL_BUDGET)],
                             out / "buyer.log", timeout=1500)
        # prices arrive as JSON numbers (floats), so spent is logged as e.g. 900.0 — the first
        # version of this regex expected an integer and silently reported FAIL (see REPORT §3)
        m = re.search(r"\[summary\] bought (\{.*?\}) spent (\d+(?:\.\d+)?) of (\d+); missing (\[.*?\])", text)
        bought = eval(m.group(1)) if m else {}
        spent, total, missing = (float(m.group(2)), int(m.group(3)), eval(m.group(4))) if m else (0, TOTAL_BUDGET, SHOPPING_LIST)
        receipts = sum(len(re.findall(r"-> accept", (out / f"shop_{k:02d}.log").read_text(encoding="utf-8"))) for k in range(1, len(bases) + 1))
        ok = not missing and spent <= total and len(bought) == len(SHOPPING_LIST) and receipts == len(bought)
        line = (f"{'PASS' if ok else 'FAIL'} list {SHOPPING_LIST} budget {total}: bought {bought}, spent {spent}, missing {missing}, "
                f"receipts across shops {receipts} (no item twice: {receipts == len(bought)})")
        print(line)
        (out / "summary.txt").write_text(line + "\n", encoding="utf-8")
        return ok
    finally:
        for p, f in procs:
            stop(p); f.close()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "1"
    ok = challenge1() if which == "1" else challenge2()
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
