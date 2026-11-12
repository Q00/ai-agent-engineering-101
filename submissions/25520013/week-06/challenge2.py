"""Challenge 2 driver: the shopper buys a shopping list from 20 shops on one budget.

    uv run python challenge2.py --seed 0 --items 4

Starts the 20 shops of shops/*.json (seller.py in catalog mode, each with its
own categories and price level) on free ports and writes their URLs to
directory.json. A seeded RNG picks one target item from each of N different
categories and writes a request from its attributes without naming the product
(challenge1.make_request), with machine-checkable constraints. The budget is
feasible but binding: for each item, the cheapest shop's most expensive fitting
item (reserve x price_factor; shops tend to offer their dearest fitting piece),
summed and times BUDGET_FACTOR. The shopper runs as a subprocess; its RESULT
line gives the receipts, and the sellers' logs count every closed deal. Passes
when every item is bought exactly once, each bought product meets its
constraints, and the total is within the budget.
"""

import argparse
import json
import pathlib
import random
import socket
import subprocess
import sys
import time

import httpx

import catalog_search
import challenge1

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = HERE / "catalog" / "catalog.json"
SHOPS = HERE / "shops"
BUDGET_FACTOR = 1.25
SHOPPER_TIMEOUT = 20 * 60  # seconds; the shopper's own limit is 15 min


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def shop_floor(item: dict, shop: dict) -> int:
    """The item's reserve at this shop (seller.shop_catalog rounds the same way)."""
    return round(item["reserve"] * shop.get("price_factor", 1))


def make_list(
    rng: random.Random, catalog: list[dict], shops: list[dict], n: int
) -> list[dict]:
    """N requests from N different categories, with each item's feasibility numbers."""
    categories = sorted({it["attrs"]["category"] for it in catalog})
    out = []
    for cat in rng.sample(categories, n):
        target = rng.choice([it for it in catalog if it["attrs"]["category"] == cat])
        request, cons = challenge1.make_request(target)
        fitting = [it for it in catalog if challenge1.satisfies(it, cons)]
        per_shop = {}
        for no, shop in enumerate(shops, 1):
            if cat in shop["categories"]:
                floors = [shop_floor(it, shop) for it in fitting]
                per_shop[no] = {"min": min(floors), "max": max(floors)}
        out.append({
            "target": target["id"], "request": request, "constraints": cons, "fitting_items": len(fitting),
            "shops_carrying": per_shop,
            "cheapest_fit": min(p["min"] for p in per_shop.values()),
            "cheapest_shop_max_fit": min(p["max"] for p in per_shop.values()),
        })  # fmt: skip
    return out


def seller_deals(log_dir: pathlib.Path) -> int:
    """Deals the sellers closed, counted from their own logs."""
    return sum(
        f.read_text().count("] move: accept") for f in log_dir.glob("shop-*.log")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--items", type=int, default=4)
    ap.add_argument("--out-dir", default="logs/challenge-2")
    args = ap.parse_args()

    catalog = catalog_search.load(CATALOG)
    by_id = {it["id"]: it for it in catalog}
    shop_files = sorted(SHOPS.glob("shop-*.json"))
    shops = [json.loads(f.read_text()) for f in shop_files]
    rng = random.Random(args.seed)
    items = make_list(rng, catalog, shops, args.items)
    budget = round(BUDGET_FACTOR * sum(i["cheapest_shop_max_fit"] for i in items))
    feasibility = {
        "sum_cheapest_fit": sum(i["cheapest_fit"] for i in items),
        "sum_cheapest_shop_max_fit": sum(i["cheapest_shop_max_fit"] for i in items),
        "budget_factor": BUDGET_FACTOR,
    }

    out = HERE / args.out_dir
    (out / "sellers").mkdir(parents=True, exist_ok=True)
    (out / "list.json").write_text(
        json.dumps([i["request"] for i in items], indent=1) + "\n"
    )
    print(
        f"list (seed {args.seed}), budget {budget}, feasibility {feasibility}",
        flush=True,
    )
    for i in items:
        print(
            f"- {i['request']} | cheapest fit {i['cheapest_fit']}, shops {list(i['shops_carrying'])}",
            flush=True,
        )

    procs, logs, urls = [], [], []
    t0 = time.monotonic()
    try:
        for f in shop_files:
            port = free_port()
            urls.append(f"http://127.0.0.1:{port}")
            logs.append(open(out / "sellers" / f"{f.stem}.log", "w"))
            procs.append(subprocess.Popen(
                [sys.executable, "seller.py", "--port", str(port), "--config", str(f.relative_to(HERE))],
                cwd=HERE, stdout=logs[-1], stderr=subprocess.STDOUT,
            ))  # fmt: skip
        up = 0
        for url in urls:
            for _ in range(60):
                try:
                    httpx.get(
                        url + "/.well-known/agent-card.json", timeout=2
                    ).raise_for_status()
                    up += 1
                    break
                except httpx.HTTPError:
                    time.sleep(1)
        print(f"{up} of {len(urls)} shops serve their Agent Card", flush=True)
        (out / "directory.json").write_text(json.dumps(urls, indent=1) + "\n")
        with open(out / "run.log", "w") as run_log:
            subprocess.run(
                [sys.executable, "-u", "shopper.py", "--directory", str(out / "directory.json"),
                 "--list", str(out / "list.json"), "--budget", str(budget)],
                cwd=HERE, stdout=run_log, stderr=subprocess.STDOUT, timeout=SHOPPER_TIMEOUT,
            )  # fmt: skip
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait()
        for f in logs:
            f.close()

    result_lines = [
        ln
        for ln in (out / "run.log").read_text().splitlines()
        if ln.startswith("RESULT ")
    ]
    result = (
        json.loads(result_lines[-1][len("RESULT ") :])
        if result_lines
        else {"bought": []}
    )
    receipts = [b for b in result["bought"] if b.get("receipt")]
    details, fits = [], True
    for no, i in enumerate(items, 1):
        got = [b for b in receipts if b["item"] == no]
        verdicts = []
        for b in got:
            item = by_id.get(b["receipt"].get("item_id"))
            ok = item is not None and challenge1.satisfies(item, i["constraints"])
            verdicts.append(ok)
            fits = fits and ok
        details.append({
            **{k: v for k, v in i.items()}, "item": no, "bought": [b["receipt"] for b in got],
            "shop": [b["shop"] for b in got], "fits": verdicts,
            "bought_attrs": [by_id[b["receipt"]["item_id"]]["attrs"] for b in got if b["receipt"].get("item_id") in by_id],
        })  # fmt: skip
    total = sum(b["receipt"]["price"] for b in receipts)
    deals = seller_deals(out / "sellers")
    duplicates = sum(max(0, len(d["bought"]) - 1) for d in details) + max(
        0, deals - len(receipts)
    )
    all_bought = all(d["bought"] for d in details)
    summary = {
        "seed": args.seed,
        "shops": up,
        "items": len(items),
        "budget": budget,
        "total": total,
        "all_bought": all_bought,
        "duplicates": duplicates,
        "fits": fits and bool(receipts),
        "seller_deals": deals,
        "feasibility": feasibility,
        "seconds": round(time.monotonic() - t0),
        "negotiations": result.get("negotiations", []),
        "details": details,
    }
    (out / "results.json").write_text(json.dumps(summary, indent=1) + "\n")
    passed = (
        up >= 15
        and all_bought
        and total <= budget
        and duplicates == 0
        and summary["fits"]
    )
    print(
        f"challenge 2: seed {args.seed}, {up} shops, {len(items)} items, budget {budget}, total {total}, "
        f"all bought {all_bought}, duplicates {duplicates}, fits {summary['fits']}, {summary['seconds']} s "
        f"-> {'PASS' if passed else 'FAIL'}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
