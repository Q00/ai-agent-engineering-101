"""Challenge 1 check: describe items to catalog_seller.py and verify what it sells.

    python catalog_seller.py                 # terminal 1
    python challenge1_run.py                 # terminal 2 (http://127.0.0.1:9301, catalog.json)

For each request: did the shop pick an item that satisfies the request, and did it
never ask or sell below that item's reserve? Writes logs/challenge-1/run-*.txt and summary-*.json.
"""
import argparse
import json
import os
import re
import time

from buyer import A2AClient, last_number, receipt_price, state, status_text

WOODEN = {"oak", "walnut", "pine", "birch plywood", "bamboo"}
METAL = {"steel", "aluminum"}

REQUESTS = [
    ("something wooden to keep about 40 books, not taller than my desk",
     lambda it: it["category"] in ("bookshelf", "bookcase", "wall shelf") and it["material"] in WOODEN
     and it.get("capacity_books", 0) >= 40 and it["height_cm"] <= 80),
    ("a dimmable metal lamp for my desk",
     lambda it: it["category"] == "desk lamp" and it["material"] in METAL and "Dimmable" in it["description"]),
    ("a tall floor lamp, at least 170 cm",
     lambda it: it["category"] == "floor lamp" and it["height_cm"] >= 170),
    ("a lightweight chair for my balcony, plastic or aluminum",
     lambda it: it["category"] in ("chair", "stool") and it["material"] in ("plastic", "aluminum")),
    ("a glass coffee table, lower than 45 cm",
     lambda it: it["category"] == "coffee table" and it["material"] == "glass" and it["height_cm"] < 45),
    ("a big white wardrobe, at least 2 meters tall",
     lambda it: it["category"] == "wardrobe" and it["color"] == "white" and it["height_cm"] >= 200),
]
OFFER_STEPS = [0.5, 0.65, 0.8, 0.9]  # fractions of the opening ask; then accept the current ask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:9301")
    ap.add_argument("--catalog", default="catalog.json")
    ap.add_argument("--markup", type=float, default=1.0)
    args = ap.parse_args()
    by_id = {it["id"]: it for it in json.load(open(args.catalog, encoding="utf-8"))}

    os.makedirs("logs/challenge-1", exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = open(f"logs/challenge-1/run-{stamp}.txt", "w", encoding="utf-8")

    def log(s):
        print(s, flush=True)
        out.write(s + "\n")

    client = A2AClient(args.url)
    log(f"# challenge 1 run {stamp}, shop {client.card.get('name')!r}, catalog {len(by_id)} items")
    results = []
    for request, fits in REQUESTS:
        log(f"\n## {request}")
        task = client.send(request)
        tid, text = task.get("id"), status_text(task)
        log(f"  <- {state(task)} | {text}")
        m = re.search(r"\b(F\d{5})\b", text)
        item = by_id.get(m.group(1)) if m else None
        if state(task) != "TASK_STATE_INPUT_REQUIRED" or item is None:
            results.append({"request": request, "item": None, "fits": False, "reserve_ok": True, "deal": None})
            log("  RESULT: no item picked")
            continue
        reserve = round(item["reserve"] * args.markup)
        log(f"  picked {item['id']} {item['name']} | {item['description']} | reserve {reserve}")
        asks = [last_number(text)]
        offers = [int(asks[0] * f) for f in OFFER_STEPS]
        deal = None
        for offer in offers + [None]:
            if deal is not None:
                break
            if offer is None or offer >= asks[-1]:
                offer = asks[-1]  # never offer more than the current ask; this accepts it
            task = client.send(str(offer), tid)
            text = status_text(task)
            log(f"  -> {offer}\n  <- {state(task)} | {text}")
            if state(task) == "TASK_STATE_COMPLETED":
                deal = receipt_price(task)
                break
            if state(task) != "TASK_STATE_INPUT_REQUIRED":
                break
            asks.append(last_number(text))
        ok_item = bool(fits(item))
        reserve_ok = all(a is None or a >= reserve for a in asks) and (deal is None or deal >= reserve)
        results.append({"request": request, "item": item["id"], "fits": ok_item,
                        "reserve": reserve, "asks": asks, "deal": deal, "reserve_ok": reserve_ok})
        log(f"  RESULT: fits={ok_item} reserve_ok={reserve_ok} asks={asks} deal={deal}")

    passed = sum(r["fits"] and r["reserve_ok"] for r in results)
    log(f"\n{passed}/{len(results)} requests: right item and reserve held")
    with open(f"logs/challenge-1/summary-{stamp}.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    out.close()


if __name__ == "__main__":
    main()