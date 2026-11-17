"""Challenge 1: the seller on a large catalog, with buyers who describe instead of naming.

    python challenge1.py --url http://127.0.0.1:9999 --out logs/challenge-1

A fixed buyer (no model) sends each request, then walks up a ladder of offers
relative to the seller's first ask (50%, 65%, 80%, 90%) and accepts the ask after
that. Each run passes when the seller picked an item that fits the request and
never asked or sold below that item's reserve (read from catalog.json).
"""
import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

from catalog import Catalog

WOOD = ("oak", "walnut", "pine", "birch", "teak", "bamboo", "beech")
REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda i: i["category"] in ("bookshelf", "cube shelf") and i.get("books", 0) >= 30
     and i.get("height_cm", 999) <= 80 and any(w in i.get("material", i["description"]).lower() for w in WOOD)),
    ("a soft seat for two people to watch TV, velvet if you have it",
     lambda i: i["category"] == "sofa" and i.get("seats", 0) >= 2),
    ("a light for reading next to my armchair, standing on the floor",
     lambda i: i["category"] == "floor lamp"),
    ("somewhere to put about 12 pairs of shoes by the door, metal please",
     lambda i: i["category"] == "shoe rack" and i.get("pairs", 0) >= 12),
    ("a table where a family of six can eat dinner, solid wood",
     lambda i: i["category"] == "dining table" and i.get("seats", 0) >= 6),
    ("I want to buy a lamp",
     lambda i: i["id"] == "lamp"),
]
LADDER = [0.5, 0.65, 0.8, 0.9]
LOG = []


def log(line=""):
    print(line, flush=True)
    LOG.append(line)


def rpc(url, method, params):
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4())[:8], "method": method, "params": params}
    r = httpx.post(url, json=body, headers={"A2A-Version": "1.0"}, timeout=200).json()
    if "error" in r:
        log(f"    <- error {r['error'].get('message', '')[:120]}")
        return None
    t = r["result"].get("task", r["result"])
    text = " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))
    log(f"    <- {t['status']['state']} | {text[:300]}")
    for a in t.get("artifacts", []):
        log(f"       artifact {json.dumps([p.get('data') for p in a.get('parts', [])])}")
    return t


def send(url, text, task_id=None):
    msg = {"role": "ROLE_USER", "messageId": str(uuid.uuid4()), "parts": [{"text": text}]}
    if task_id:
        msg["taskId"] = task_id
    log(f"    -> {text!r}")
    return rpc(url, "SendMessage", {"message": msg})


def text_of(t):
    return " ".join(p.get("text", "") for p in t["status"].get("message", {}).get("parts", []))


def last_number(t):
    nums = re.findall(r"\d+", text_of(t))
    return int(nums[-1]) if nums else None


def item_of(t, catalog):
    m = re.search(r"\[([a-z\-]+-\d{4})\]", text_of(t))
    if m:
        return catalog.by_id.get(m.group(1))
    return catalog.exact(text_of(t).split(" My price")[0].split()[-1]) if text_of(t) else None


def run(url, catalog, request, check):
    started = time.time()
    t = send(url, request)
    if t is None or t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
        return False, "seller did not open"
    item = item_of(t, catalog)
    if item is None:
        return False, "could not tell which item the seller offered"
    tid, first, asks = t["id"], last_number(t), []
    ladder = [round(first * f) for f in LADDER]
    while t is not None and t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED" and len(asks) < 8:
        ask = last_number(t)
        asks.append(ask)
        offer = ladder.pop(0) if ladder else ask
        t = send(url, str(offer), tid)
    price = None
    for a in (t or {}).get("artifacts", []):
        for p in a.get("parts", []):
            if isinstance(p.get("data"), dict):
                price = p["data"].get("price")
    fits, low = check(item), [a for a in asks if a < item["reserve"]]
    sold_low = price is not None and price < item["reserve"]
    ok = fits and not low and not sold_low
    return ok, (f"item {item['id']} ({item['name']}, reserve {item['reserve']}) fits={fits}; asks {asks}; "
                f"final {t['status']['state'] if t else 'error'}, price {price}; {time.time() - started:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9999")
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--out", default="logs/challenge-1")
    a = ap.parse_args()
    catalog = Catalog(a.catalog)
    card = httpx.get(a.url.rstrip("/") + "/.well-known/agent-card.json", timeout=30).json()
    url = card["supportedInterfaces"][0]["url"]
    log(f"# challenge 1, {time.strftime('%Y-%m-%d %H:%M:%S')}, seller {card['name']!r}, catalog {len(catalog.items)} items")
    results = []
    for n, (request, check) in enumerate(REQUESTS, 1):
        log(f"\n## {n}. {request}")
        ok, detail = run(url, catalog, request, check)
        results.append({"request": request, "pass": ok, "detail": detail})
        log(f"  {'PASS' if ok else 'FAIL'}  {detail}")
    passed = sum(r["pass"] for r in results)
    log(f"\n{passed}/{len(results)} passed")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%m%d-%H%M")
    (out / f"run-{stamp}.log").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    (out / f"run-{stamp}.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
