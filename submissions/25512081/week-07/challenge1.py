"""Challenge 1: the seller on the 3,200-item catalog, judged mechanically.

    python challenge1.py            # -> logs/challenge-1/{seller.log, run.log, summary.json}

Starts seller.py on catalog.json and sends each request below as a buyer would:
a description, never the item's name. The seller sees only the words; the spec
(what a right item must satisfy) stays here, in the judge.

A request passes when the item the seller offers satisfies its spec (or, for the
request we cannot serve, the seller declines), no ask is below that item's
reserve, and a deal (if any) is on the offered item at or above its reserve.
After the first ask the judge plays a fixed buyer: offers 60/72/82/90% of the
opening ask and accepts any ask within 95% of it, up to 8 messages.
"""
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from a2a_client import A2AError, Peer

OUT = Path("logs/challenge-1")
CATALOG = {it["id"]: it for it in json.load(open("catalog.json", encoding="utf-8"))["items"]}
BY_NAME = {it["name"]: it for it in CATALOG.values()}
WOOD = {"oak", "pine", "walnut", "teak", "birch", "bamboo", "beech"}
METAL = {"steel", "iron", "aluminum"}

REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda i: i["category"] == "bookshelf" and i["material"] in WOOD
     and i["capacity_books"] >= 40 and i["height_cm"] <= 76),
    ("a high seat for the kitchen counter, metal if possible",
     lambda i: i["category"] == "bar stool" and i["counter_height"]),
    ("a rug I can throw in the washing machine, for a hallway",
     lambda i: i["category"] == "rug" and i["washable"]),
    ("a sofa that turns into a bed for guests, room for three people",
     lambda i: i["category"] == "sofa" and i["sleeper"] and i["seats"] >= 3),
    ("a dimmable light for reading at my desk, at least 600 lumens",
     lambda i: i["category"] == "desk lamp" and i["dimmable"] and i["brightness_lm"] >= 600),
    ("a dining table for six that can be made bigger for holidays",
     lambda i: i["category"] == "dining table" and i["seats"] >= 6 and i["extendable"]),
    ("a desk I can stand at, at least 140 cm wide",
     lambda i: i["category"] == "desk" and i["standing"] and i["width_cm"] >= 140),
    ("a leather chair for my home office, with armrests",
     lambda i: i["category"] == "office chair" and i["material"] == "leather" and i["armrests"]),
    ("a tall wardrobe with a mirror on it",
     lambda i: i["category"] == "wardrobe" and i["mirror"] and i["height_cm"] >= 190),
    ("a low stand for a 65 inch TV with some storage",
     lambda i: i["category"] == "tv stand" and i["max_tv_in"] >= 65 and i["storage"]),
    ("a grand piano for my living room", None),            # nothing fits: the seller should decline
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    seller_log = open(OUT / "seller.log", "w")
    proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port), "--catalog",
                             "catalog.json"], stdout=seller_log, stderr=subprocess.STDOUT)
    lines, results = [], []

    def log(m):
        print(m, flush=True)
        lines.append(m)

    try:
        for _ in range(60):
            try:
                peer = Peer(f"http://127.0.0.1:{port}")
                break
            except Exception:
                time.sleep(0.5)
        log(f"# challenge 1: {len(CATALOG)}-item catalog, seller card '{peer.card['name']}'")
        for n, (text, spec) in enumerate(REQUESTS, 1):
            res = run_one(peer, n, text, spec, log)
            results.append(res)
            log(f"  => {'PASS' if res['pass'] else 'FAIL'}: {res['why']}\n")
    finally:
        proc.terminate()
        passed = sum(r["pass"] for r in results)
        log(f"# {passed}/{len(results)} passed")
        (OUT / "run.log").write_text("\n".join(lines) + "\n")
        json.dump({"passed": passed, "total": len(results), "results": results},
                  open(OUT / "summary.json", "w"), indent=2)


def run_one(peer, n, text, spec, log):
    log(f"[R{n:02d}] buyer: {text!r}")
    t0 = time.time()
    r = peer.send(text)
    log(f"   <- {r.state} | {r.text}")
    if r.state != "TASK_STATE_INPUT_REQUIRED":
        ok = spec is None
        return {"n": n, "request": text, "pass": ok, "item": None,
                "why": "declined, as expected" if ok else f"no offer ({r.state})"}
    m = re.match(r"(.*?): asking", r.text)
    item = BY_NAME.get(m.group(1)) if m else None
    if item is None:
        return {"n": n, "request": text, "pass": False, "item": None,
                "why": f"could not tell which item was offered: {r.text!r}"}
    log(f"   offered {item['id']} {item['name']} (reserve {item['reserve']}): {item['description']}")
    fits = spec(item) if spec else False
    opening, asks = r.ask, [r.ask]
    ladder = [round(opening * f) for f in (0.6, 0.72, 0.82, 0.9)]
    for _ in range(8):
        if r.state != "TASK_STATE_INPUT_REQUIRED":
            break
        offer = r.ask if r.ask is not None and r.ask <= 0.95 * opening else (
            ladder.pop(0) if ladder else round(0.9 * opening))
        r = peer.send(str(offer), r.id)
        log(f"   -> {offer}  <- {r.state} | {r.text}")
        if r.state == "TASK_STATE_INPUT_REQUIRED" and r.ask is not None:
            asks.append(r.ask)
    if r.state == "TASK_STATE_INPUT_REQUIRED":
        try:
            peer.cancel(r.id)
        except A2AError:
            pass
    low = [a for a in asks if a is not None and a < item["reserve"]]
    deal_ok = True
    if r.price is not None:
        deal_ok = r.receipt.get("item_id") == item["id"] and r.price >= item["reserve"]
    ok = fits and not low and deal_ok
    why = (f"{'fits' if fits else 'WRONG ITEM'}; asks {asks} vs reserve {item['reserve']}"
           f"{' BELOW RESERVE' if low else ''}; "
           + (f"deal at {r.price}" if r.price is not None else f"ended {r.state}")
           + f"; {time.time() - t0:.0f}s")
    return {"n": n, "request": text, "pass": ok, "item": item["id"], "fits": fits,
            "asks": asks, "deal": r.price, "why": why}


if __name__ == "__main__":
    main()
