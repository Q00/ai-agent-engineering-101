"""Week 06, challenge 1 -- buy from the 3513-item catalogue by describing it.

The trade test only ever names three items. This script is the other half:
buyers who describe what they want and never say the word, which is what the
challenge asks for. It starts the shop, runs `buyer.py` against it once per
request, and checks the two things the challenge calls failures -- the wrong
item, and an ask below that item's reserve.

    python challenge1.py          # writes logs/challenge-1/

The reserve check is done here, outside the shop, on the shop's own log: a
guarantee the seller makes about itself is worth less than one a second
program verifies from the outside.
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

HERE = Path(__file__).parent
LOGS = HERE / "logs" / "challenge-1"

# (what the buyer says, budget, the category it ought to come back with)
REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk", 260,
     {"bookshelf", "shelf", "cabinet"}),
    ("a comfy seat for two people in the living room", 700, {"sofa", "bench"}),
    ("a tall mirror for the hallway", 300, {"mirror"}),
    ("a small bedside table in pale wood", 200, {"nightstand", "table", "coffee table"}),
    ("a rattan stool for the kitchen", 90, {"stool", "chair"}),
    ("an inflatable kayak with two paddles", 400, set()),      # nothing like it: expect a refusal
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_card(base: str, seconds: int = 60):
    end = time.time() + seconds
    while time.time() < end:
        try:
            r = httpx.get(base + "/.well-known/agent-card.json", timeout=3)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(1)
    return None


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    catalog.load()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    seller_log = (LOGS / "seller.log").open("w", encoding="utf-8")
    seller = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)],
                              cwd=HERE, stdout=seller_log, stderr=subprocess.STDOUT)
    results = []
    try:
        card = wait_card(base)
        if card is None:
            print("the shop never served its card")
            return 1
        print(f"shop up: {card['name']} -- {card['description'][:70]}...")

        for i, (text, budget, want) in enumerate(REQUESTS, 1):
            out = (LOGS / f"request-{i}.log").open("w", encoding="utf-8")
            out.write(f"request: {text}\nbudget: {budget}\n\n")
            out.flush()
            print(f"\n[{i}] {text!r} (budget {budget})")
            subprocess.run([sys.executable, "buyer.py", base, text, str(budget)],
                           cwd=HERE, stdout=out, stderr=subprocess.STDOUT, timeout=300)
            out.close()
            body = (LOGS / f"request-{i}.log").read_text(encoding="utf-8")
            bought = re.search(r"bought .* at ([\d.]+)", body)
            results.append({"request": text, "budget": budget, "expect": sorted(want),
                            "bought_at": float(bought.group(1)) if bought else None,
                            "log": f"request-{i}.log"})
            print("   ", body.strip().splitlines()[-1] if body.strip() else "(no output)")
    finally:
        seller.terminate()
        try:
            seller.wait(timeout=10)
        except Exception:
            seller.kill()
        seller_log.close()

    # --- check the shop from outside, on its own log -----------------------
    shop = (LOGS / "seller.log").read_text(encoding="utf-8")
    chosen = re.findall(r"chose (\S+) '([^']*)' reserve (\d+)", shop)
    outright = re.findall(r"'([^']*)' names (\S+) outright", shop)
    asks = re.findall(r"list price (\S+): .* -> (\d+)", shop)
    reserves = {i["id"]: i["reserve"] for i in catalog.load()}

    problems = []
    for item_id, _name, reserve in chosen:
        if int(reserve) != reserves.get(item_id):
            problems.append(f"{item_id}: logged reserve {reserve} != catalogue "
                            f"{reserves.get(item_id)}")
    for item_id, ask in asks:
        if int(ask) < reserves.get(item_id, 0):
            problems.append(f"{item_id}: list price {ask} below reserve {reserves[item_id]}")

    summary = {"catalogue_size": len(catalog.load()),
               "requests": results,
               "items_named_outright": len(outright),
               "items_chosen_by_harness": len(chosen),
               "list_prices": {k: int(v) for k, v in asks},
               "reserve_violations": problems}
    (LOGS / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")

    print(f"\ncatalogue {len(catalog.load())} items | chosen by harness {len(chosen)} | "
          f"reserve violations {len(problems)}")
    for p in problems:
        print("  !", p)
    print(f"logs in {LOGS}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
