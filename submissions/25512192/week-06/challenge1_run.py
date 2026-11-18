"""Challenge 1: the seller on the 3024-item catalog, buyers who describe instead of naming.

    python challenge1_run.py            # writes logs/challenge-1/run.log and seller.log

Starts seller.py --catalog catalogs/large.json, then plays a fixed buyer for each request
below: it offers 50%, 65%, 80%, 90% of the opening ask and accepts any ask within 90% of the
opening ask. The checker (not the seller) then reads the catalog and checks
  - the item the seller picked fits the request (a predicate per request), and
  - no ask and no deal was below that item's reserve.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from a2a_client import Shop, ask_of, receipt, state, text_of

HERE = Path(__file__).parent
OUT = HERE / "logs" / "challenge-1"
PORT = 9471
CATALOG = {i["id"]: i for i in json.load(open(HERE / "catalogs" / "large.json", encoding="utf-8"))}

REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda i: i["category"] in ("bookshelf", "wall shelf") and i["attrs"]["wooden"]
     and i["attrs"]["books"] >= 30 and i["attrs"]["height_cm"] <= 80),
    ("a bright light for reading at my desk, at least 600 lumen",
     lambda i: i["category"] == "desk lamp" and i["attrs"]["lumens"] >= 600),
    ("a wooden table where four of us can have dinner",
     lambda i: i["category"] == "dining table" and i["attrs"]["wooden"] and i["attrs"]["seats"] >= 4),
    ("a big wool rug for the living room, roughly two by three meters",
     lambda i: i["category"] == "rug" and i["attrs"]["material"] == "wool" and i["attrs"]["width_cm"] >= 160),
    ("a chair for long work days at the computer; I weigh 130 kg",
     lambda i: i["category"] == "office chair" and i["attrs"]["max_user_kg"] >= 130),
    ("something to put my 65 inch TV on",
     lambda i: i["category"] == "tv stand" and i["attrs"]["max_tv_inch"] >= 65),
    ("a lockable cabinet for A4 paper folders, three drawers",
     lambda i: i["category"] == "filing cabinet" and i["attrs"]["drawers"] == 3),
    ("a full length mirror, metal frame",
     lambda i: i["category"] == "mirror" and i["attrs"]["height_cm"] > 140 and not i["attrs"]["wooden"]),
]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    run_log = open(OUT / "run.log", "w", encoding="utf-8")

    def log(line=""):
        print(line, flush=True)
        run_log.write(line + "\n")
        run_log.flush()

    seller_log = open(OUT / "seller.log", "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(PORT), "--catalog", "catalogs/large.json"],
                            cwd=HERE, stdout=seller_log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/.well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        shop = Shop(f"http://127.0.0.1:{PORT}")
        size = (HERE / "catalogs" / "large.json").stat().st_size
        log(f"# challenge 1: {len(CATALOG)} items ({size // 1024} KB of JSON) behind {shop.name}")
        score = {"right_item": 0, "reserve_ok": 0, "deals": 0}
        for n, (request, fits) in enumerate(REQUESTS, 1):
            log(f"\n## request {n}: {request!r}")
            t = shop.send(request)
            tid, opening = t.get("id"), ask_of(t)
            log(f"  <- {state(t)} | {text_of(t)}")
            m = re.search(r"\b[A-Z]{2,3}-\d{3}\b", text_of(t))
            item = CATALOG.get(m.group()) if m else None
            if item is None:
                log("  no item picked")
                continue
            asks, budget = [opening], round(opening * 0.9)
            ladder = [round(opening * f) for f in (0.5, 0.65, 0.8, 0.9)]
            while state(t) == "TASK_STATE_INPUT_REQUIRED" and len(asks) <= 8:
                ask = ask_of(t)
                offer = ask if ask <= budget else (ladder.pop(0) if ladder else budget)
                t = shop.send(str(offer), tid)
                log(f"  -> {offer} | <- {state(t)} ask={ask_of(t)} | {text_of(t)}")
                if state(t) == "TASK_STATE_INPUT_REQUIRED":
                    asks.append(ask_of(t))
            if state(t) == "TASK_STATE_INPUT_REQUIRED":
                shop.cancel(tid)
            r = receipt(t)
            right = fits(item)
            low = [a for a in asks if a < item["reserve"]] + ([r["price"]] if r and r["price"] < item["reserve"] else [])
            score["right_item"] += right
            score["reserve_ok"] += not low
            score["deals"] += r is not None
            log(f"  picked {item['id']} ({item['description']}) attrs={json.dumps(item['attrs'])}")
            log(f"  CHECK item fits request: {'OK' if right else 'WRONG ITEM'} | reserve {item['reserve']}, "
                f"asks {asks}, deal {r and r['price']} -> {'OK' if not low else 'BELOW RESERVE ' + str(low)}")
        log(f"\nSUMMARY {len(REQUESTS)} requests: right item {score['right_item']}, reserve held {score['reserve_ok']}, "
            f"deals {score['deals']}")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        seller_log.close()
        run_log.close()


if __name__ == "__main__":
    main()
