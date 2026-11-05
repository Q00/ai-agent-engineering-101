"""Challenge 1 driver: buyers describe items to the catalog seller; check what it offers.

    uv run python challenge1.py --seed 0 --trials 8
    uv run python challenge1.py --search-only --trials 500   # offline: search recall, no harness

For each trial a seeded RNG picks a target item and writes a request from its
attributes without naming the product ("something wooden to keep at least 40
books, not taller than 120 cm"), with machine-checkable constraints. A
rule-based buyer negotiates over A2A (JSON-RPC, A2A-Version 1.0, taskId
follow-ups). A trial is correct when every item the seller offers or sells meets
the constraints; any ask or sale below that item's reserve counts as a low ask.
"""

import argparse
import json
import math
import pathlib
import random
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import buyer
import catalog_search
import httpx
import seller

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = HERE / "catalog" / "catalog.json"
SHOP = HERE / "catalog" / "shop.json"
MAX_MESSAGES = 8
LADDER = (0.6, 0.7, 0.8, 0.9, 1.0)  # offers as fractions of the budget

MATERIAL_WORDS = {
    "wood": "wooden",
    "metal": "metal",
    "glass": "glass",
    "marble": "marble",
    "ceramic": "ceramic",
    "rattan": "rattan",
    "leather": "leather",
    "fabric": "fabric-upholstered",
    "wool": "wool",
    "cotton": "cotton",
    "jute": "jute",
}

# category -> (request template, lower-bound attr, its step, upper-bound attr, its step).
# {mat} is the material phrase; {lo} and {hi} are the rounded bounds. A step of 0
# means "equal to the target's value" (mattress size).
REQUESTS = {
    "desk lamp": ("a {mat} light to stand on my desk for reading, at least {lo} lumens, no taller than {hi} cm",
                  "lumens", 100, "height_cm", 10),
    "floor lamp": ("a tall {mat} light that stands on the floor in a corner of my living room, "
                   "at least {lo} lumens, no taller than {hi} cm", "lumens", 100, "height_cm", 10),
    "pendant lamp": ("a {mat} light to hang from the ceiling above my dining table, at least {lo} lumens, "
                     "no wider than {hi} cm", "lumens", 100, "width_cm", 10),
    "office chair": ("something {mat} to sit on at my desk through long workdays, rated for at least {lo} kg, "
                     "no wider than {hi} cm", "max_load_kg", 10, "width_cm", 5),
    "dining chair": ("a {mat} seat to go around my dining table, rated for at least {lo} kg, "
                     "no taller than {hi} cm", "max_load_kg", 10, "height_cm", 5),
    "armchair": ("a comfortable {mat} seat for one person to read in, with a seat at least {lo} cm wide, "
                 "no wider than {hi} cm overall", "seat_width_cm", 5, "width_cm", 5),
    "stool": ("a {mat} backless seat for my kitchen counter, seat at least {lo} cm off the floor, "
              "no wider than {hi} cm", "seat_height_cm", 5, "width_cm", 5),
    "bookshelf": ("something {mat} to keep at least {lo} books, not taller than {hi} cm",
                  "books", 10, "height_cm", 10),
    "desk": ("a {mat} surface to work at with my computer from home, at least {lo} cm wide, "
             "no deeper than {hi} cm", "worktop_width_cm", 10, "depth_cm", 5),
    "dining table": ("a {mat} table for family meals that seats at least {lo} people, no longer than {hi} cm",
                     "seats", 2, "width_cm", 10),
    "coffee table": ("a low {mat} table to put in front of my couch, at least {lo} cm long, "
                     "no higher than {hi} cm", "width_cm", 10, "height_cm", 5),
    "sofa": ("a {mat} seat for at least {lo} people in my living room, no wider than {hi} cm",
             "seats", 1, "width_cm", 10),
    "bed frame": ("something {mat} to put my {lo} mattress on, no taller than {hi} cm",
                  "mattress", 0, "height_cm", 10),
    "wardrobe": ("somewhere {mat} to hang my clothes in the bedroom, at least {lo} cm of hanging rail, "
                 "no taller than {hi} cm", "hanging_rail_cm", 10, "height_cm", 10),
    "cabinet": ("{mat} closed storage with doors for my hall, at least {lo} shelves inside, "
                "no taller than {hi} cm", "shelves", 1, "height_cm", 10),
    "nightstand": ("a small {mat} piece to stand beside my bed, with at least {lo} drawers, "
                   "no taller than {hi} cm", "drawers", 1, "height_cm", 5),
    "tv stand": ("something {mat} to put a {lo}-inch television on, no wider than {hi} cm",
                 "max_tv_inch", 1, "width_cm", 10),
    "shoe rack": ("something {mat} by my front door to hold at least {lo} pairs of shoes, "
                  "no taller than {hi} cm", "pairs", 2, "height_cm", 10),
    "mirror": ("something with a {mat} frame to see myself in, at least {lo} cm tall, no wider than {hi} cm",
               "height_cm", 10, "width_cm", 10),
    "rug": ("something soft and {mat} for my living room floor, at least {lo} cm wide, no longer than {hi} cm",
            "width_cm", 10, "depth_cm", 10),
}  # fmt: skip


def make_request(item: dict) -> tuple[str, dict]:
    """A natural-language request the item satisfies, and its machine-checkable constraints."""
    a = item["attrs"]
    template, lo_attr, lo_step, hi_attr, hi_step = REQUESTS[a["category"]]
    lo = a[lo_attr] if lo_step == 0 else max(lo_step, a[lo_attr] // lo_step * lo_step)
    hi = math.ceil(a[hi_attr] / hi_step) * hi_step
    text = template.format(mat=MATERIAL_WORDS[a["material_family"]], lo=lo, hi=hi)
    cons = {
        "category": a["category"],
        "material_family": a["material_family"],
        "min": {lo_attr: lo} if lo_step else {},
        "equal": {} if lo_step else {lo_attr: lo},
        "max": {hi_attr: hi},
    }
    return text[0].upper() + text[1:] + ".", cons


def satisfies(item: dict, cons: dict) -> bool:
    a = item["attrs"]
    return (
        a["category"] == cons["category"]
        and a["material_family"] == cons["material_family"]
        and all(a.get(k, -math.inf) >= v for k, v in cons["min"].items())
        and all(a.get(k) == v for k, v in cons["equal"].items())
        and all(a.get(k, math.inf) <= v for k, v in cons["max"].items())
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def offered_id(text: str):
    """The catalog id the seller names in its message, e.g. "Offering: X (C00042)."."""
    found = [w.strip("().,") for w in text.split() if w.strip("().,").startswith("C")]
    found = [w for w in found if len(w) == 6 and w[1:].isdigit()]
    return found[-1] if found else None


def run_trial(n: int, url: str, target: dict, by_id: dict, catalog: list[dict]) -> dict:
    request, cons = make_request(target)
    matching = [it for it in catalog if satisfies(it, cons)]
    budget = math.ceil(1.2 * max(it["reserve"] for it in matching))
    lines = [
        f"## trial {n}: target {target['id']} {target['name']!r} (reserve {target['reserve']})",
        f"request: {request}",
        f"constraints: {json.dumps(cons)}; {len(matching)} catalog items match; budget {budget}",
    ]
    client = buyer.Seller(url)
    task = client.send(request)
    ladder, asks, offered, sent, receipt = (
        [round(budget * f) for f in LADDER],
        [],
        [],
        1,
        None,
    )
    while True:
        state, text = task["status"]["state"], buyer.status_text(task)
        lines.append(f"<- {state} | {text}")
        oid = offered_id(text)
        if oid and oid not in offered:
            offered.append(oid)
        if state != buyer.ACTIVE:
            for a in task.get("artifacts", []):
                for p in a.get("parts", []):
                    if isinstance(p.get("data"), dict):
                        receipt = p["data"]
                        lines.append(f"   receipt: {json.dumps(receipt)}")
            break
        ask = buyer.last_number(text)
        asks.append({"ask": ask, "item": oid})
        if sent >= MAX_MESSAGES:
            client.cancel(task)
            lines.append("-> CancelTask (message limit)")
            state = "TASK_STATE_CANCELED"
            break
        offer = (
            ask
            if ask is not None and ask <= budget
            else (ladder.pop(0) if ladder else budget)
        )
        lines.append(f"-> {offer}")
        task = client.send(str(offer), task)
        sent += 1
    if receipt and receipt.get("item_id") and receipt["item_id"] not in offered:
        offered.append(receipt["item_id"])

    low = [
        a for a in asks if a["item"] in by_id and a["ask"] < by_id[a["item"]]["reserve"]
    ]
    low_asks = len(low) + (
        1 if receipt and receipt["price"] < by_id[receipt["item_id"]]["reserve"] else 0
    )
    unknown = [a for a in asks if a["item"] not in by_id]
    verdicts = {oid: satisfies(by_id[oid], cons) for oid in offered if oid in by_id}
    correct = (
        bool(offered)
        and len(verdicts) == len(offered)
        and all(verdicts.values())
        and not unknown
    )
    for oid in offered:
        it = by_id.get(oid)
        if it is None:
            lines.append(f"offered {oid}: not in catalog")
            continue
        lines.append(
            f"offered {oid} {it['name']!r} reserve {it['reserve']} attrs {json.dumps(it['attrs'])} "
            f"-> {'meets' if verdicts[oid] else 'VIOLATES'} the request"
        )
    lines.append(
        f"asks {[a['ask'] for a in asks]}; final {state}; price {receipt and receipt['price']}; "
        f"low asks {low_asks}; verdict {'CORRECT' if correct else 'WRONG'}"
    )
    return {
        "trial": n,
        "target": target["id"],
        "request": request,
        "constraints": cons,
        "matching_items": len(matching),
        "budget": budget,
        "offered": offered,
        "asks": asks,
        "state": state,
        "price": receipt and receipt["price"],
        "receipt": receipt,
        "correct": correct,
        "low_asks": low_asks,
        "log": lines,
    }


def search_only(catalog: list[dict], targets: list[dict], k: int) -> None:
    """Offline: how often the top-k candidates hold at least one item that fits the request."""
    hits = 0
    for t in targets:
        request, cons = make_request(t)
        cands = catalog_search.search(catalog, request, k=k)
        ok = any(satisfies(c, cons) for c in cands)
        hits += ok
        if not ok:
            print(f"MISS {t['id']}: {request}")
    print(
        f"search recall@{k}: {hits}/{len(targets)} requests have a fitting item among the candidates"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--trials", type=int, default=8)
    ap.add_argument("--out-dir", default="logs/challenge-1")
    ap.add_argument("--port", type=int)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--search-only", action="store_true")
    args = ap.parse_args()

    catalog = catalog_search.load(CATALOG)
    by_id = {it["id"]: it for it in catalog}
    rng = random.Random(args.seed)
    targets = rng.sample(catalog, args.trials)
    if args.search_only:
        search_only(catalog, targets, seller.CANDIDATES)
        return 0

    out = HERE / args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    port = args.port or free_port()
    url = f"http://127.0.0.1:{port}"
    seller_log = open(out / "seller.log", "w")
    proc = subprocess.Popen(
        [
            sys.executable,
            "seller.py",
            "--port",
            str(port),
            "--config",
            str(SHOP.relative_to(HERE)),
        ],
        cwd=HERE,
        stdout=seller_log,
        stderr=subprocess.STDOUT,
    )
    t0 = time.monotonic()
    try:
        for _ in range(60):
            try:
                httpx.get(
                    url + "/.well-known/agent-card.json", timeout=2
                ).raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            print("no Agent Card within 60 s")
            return 1
        lock = threading.Lock()

        def one(i_t):
            r = run_trial(i_t[0], url, i_t[1], by_id, catalog)
            with lock:
                print("\n".join(r["log"]) + "\n", flush=True)
            return r

        with ThreadPoolExecutor(args.parallel) as pool:
            details = list(pool.map(one, enumerate(targets, 1)))
    finally:
        proc.terminate()
        proc.wait()
        seller_log.close()

    summary = {
        "seed": args.seed,
        "trials": len(details),
        "correct": sum(d["correct"] for d in details),
        "low_asks": sum(d["low_asks"] for d in details),
        "deals": sum(d["state"] == "TASK_STATE_COMPLETED" for d in details),
        "seconds": round(time.monotonic() - t0),
        "details": [{k: v for k, v in d.items() if k != "log"} for d in details],
    }
    head = (
        f"challenge 1: seed {args.seed}, {summary['trials']} trials, correct {summary['correct']}, "
        f"low asks {summary['low_asks']}, deals {summary['deals']}, {summary['seconds']} s"
    )
    (out / "run.log").write_text(
        head + "\n\n" + "\n\n".join("\n".join(d["log"]) for d in details) + "\n"
    )
    (out / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(head)
    return (
        0 if summary["correct"] == summary["trials"] and summary["low_asks"] == 0 else 1
    )


if __name__ == "__main__":
    sys.exit(main())
