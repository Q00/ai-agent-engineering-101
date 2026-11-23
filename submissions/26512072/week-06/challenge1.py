"""Challenge 1: the seller on a ~3000-item catalog, buyers describe items instead of naming them.

  python challenge1.py            # starts the catalog seller, writes logs/challenge-1/

A fixed buyer (the trade test's ladder idea) sends a description built from a hidden target
item's attributes, then walks up an offer ladder to 1.15 x the target's reserve. Checks:
  - the item sold (or quoted) satisfies every constraint in the description (category,
    material family, capacity, height limit); selling something else is a failure;
  - no ask below the reserve of the item the seller picked, and no deal below it.
"""
import json
import math
import random
import re
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
OUT = HERE / "logs" / "challenge-1"
N = 12
TEMPLATES = {  # category -> (description template, capacity tolerance as a fraction, height limit?)
    "bookshelf": ("something {family} to keep about {cap} books, not taller than {hmax} cm", 0.2, True),
    "floor lamp": ("a {family} standing light for a living room corner, around {cap} lumens", 0.0, False),
    "dining table": ("a {family} table where {cap} people can eat together", 0.0, False),
    "shoe rack": ("something {family} by the door for about {cap} pairs of shoes", 0.2, False),
    "tv stand": ("a low {family} unit for a {cap} inch television", 0.1, False),
    "sofa": ("a {family} couch that seats {cap}", 0.0, False),
    "wardrobe": ("a tall {family} closet for about {cap} hangers", 0.2, False),
    "nightstand": ("a small {family} bedside table with {cap} drawers", 0.0, False),
}
LOG = []


def log(line=""):
    print(line, flush=True)
    LOG.append(line)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rpc(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": uuid.uuid4().hex, "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex[:8], "method": "SendMessage", "params": {"message": msg}}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
    t = r.get("result", {}).get("task", r.get("result", {}))
    txt = " ".join(p.get("text", "") for p in t.get("status", {}).get("message", {}).get("parts", []))
    log(f"    -> {text!r}  <- {t.get('status', {}).get('state')} | {txt}")
    return t, txt


def last_number(text):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    return math.ceil(float(nums[-1])) if nums else None


def receipt(t):
    for a in t.get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict) and "price" in p["data"]:
                return p["data"]
    return None


def fits(item, target, tol, hmax):
    if item["category"] != target["category"] or item["family"] != target["family"]:
        return False
    if target["capacity"] and abs(item["capacity"] - target["capacity"]) > tol * target["capacity"]:
        return False
    return hmax is None or item["height_cm"] <= hmax


def main():
    items = json.load(open(HERE / "catalog.json", encoding="utf-8"))
    by_id = {i["id"]: i for i in items}
    rng = random.Random(61)
    targets = [rng.choice([i for i in items if i["category"] == c]) for c in TEMPLATES for _ in range(2)][:N * 2]
    rng.shuffle(targets)
    targets = targets[:N]
    OUT.mkdir(parents=True, exist_ok=True)
    port = free_port()
    seller_log = open(OUT / "seller.log", "w")
    proc = subprocess.Popen([sys.executable, "-u", "seller.py", "--port", str(port), "--catalog", "catalog.json",
                             "--name", "Big Catalog Store"], cwd=HERE, stdout=seller_log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/"
    try:
        for _ in range(60):
            try:
                if httpx.get(url + ".well-known/agent-card.json", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(1)
        log(f"# challenge 1: catalog of {len(items)} items ({(HERE / 'catalog.json').stat().st_size} bytes), "
            f"{N} described requests, seller on :{port}")
        rows = []
        for k, tg in enumerate(targets, 1):
            tmpl, tol, has_h = TEMPLATES[tg["category"]]
            hmax = tg["height_cm"] + 5 if has_h else None
            desc = tmpl.format(family=tg["family"], cap=tg["capacity"], hmax=hmax)
            budget = math.ceil(tg["reserve"] * 1.15)
            ladder = [math.ceil(budget * f) for f in (0.6, 0.75, 0.85, 0.95, 1.0)]
            log(f"\n## request {k}: \"{desc}\" (hidden target {tg['id']} {tg['name']}, reserve {tg['reserve']}, "
                f"buyer budget {budget})")
            t, txt = rpc(url, f"I want to buy {desc}")
            asks, picked = [], None
            for _ in range(8):
                if t.get("status", {}).get("state") != "TASK_STATE_INPUT_REQUIRED":
                    break
                ask = last_number(txt)
                asks.append(ask)
                offer = ask if ask is not None and ask <= budget else (ladder.pop(0) if ladder else budget)
                t, txt = rpc(url, str(offer), t["id"])
            st = t.get("status", {}).get("state")
            r = receipt(t)
            if r:
                picked = by_id.get(r.get("item_id"))
            else:  # no deal: which item did the seller quote? read it from the seller's log
                seller_log.flush()
                m = re.findall(rf"\[{t['id'][:8]}\] item (c\d+)", (OUT / "seller.log").read_text())
                picked = by_id.get(m[0]) if m else None
            ok_item = picked is not None and fits(picked, tg, tol, hmax)
            low = [a for a in asks if picked and a is not None and a < picked["reserve"]]
            sold_below = bool(r and picked and r["price"] < picked["reserve"])
            ok = ok_item and not low and not sold_below
            rows.append({"request": desc, "target": tg["id"], "picked": picked and picked["id"],
                         "picked_name": picked and picked["name"], "picked_reserve": picked and picked["reserve"],
                         "state": st, "price": r and r["price"], "asks": asks, "item_fits": ok_item,
                         "ask_below_reserve": bool(low), "sold_below_reserve": sold_below, "pass": ok})
            log(f"  {'PASS' if ok else 'FAIL'}  picked {picked and picked['id']} ({picked and picked['name']}), "
                f"reserve {picked and picked['reserve']}, fits={ok_item}, {st}, price {r and r['price']}, asks {asks}")
        passed = sum(r["pass"] for r in rows)
        log(f"\n{passed}/{len(rows)} passed; wrong items {sum(not r['item_fits'] for r in rows)}, "
            f"asks below reserve {sum(r['ask_below_reserve'] for r in rows)}, "
            f"sales below reserve {sum(r['sold_below_reserve'] for r in rows)}, "
            f"deals {sum(r['state'] == 'TASK_STATE_COMPLETED' for r in rows)}")
        (OUT / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        seller_log.close()
        (OUT / "challenge1.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
