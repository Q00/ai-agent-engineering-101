"""Challenge 2: discover ~20 A2A shops and purchase a multi-item shopping list.

Uses a GroqCloud model to choose shops based on Agent Card skills; runs
negotiations concurrently; enforces one shared total budget through reserved
per-item ceilings and an auditable ledger. Genuine runs are saved in logs/.

Windows example:
    python challenge_multi_shop.py --spawn-demo-shops 20 --total-budget 750
Or provide a JSON directory with URL strings / objects:
    python challenge_multi_shop.py --directory shops.json --total-budget 750
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from threading import Lock

import httpx
from buyer import buy
from llm_harness import decide

HERE = Path(__file__).resolve().parent
SELECT_POLICY = """You are an autonomous shopping coordinator. Choose a store based on
its Agent Card's published shopping/negotiation skill and whether it serves the requested item.
Return JSON {\"action\":\"choose\", \"sku\":\"store-id\", \"price\":0}.
Only choose an id explicitly included in candidates; consider independently managed tasks.
"""


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch_shop(url, id):
    with httpx.Client(timeout=7) as c:
        data = c.get(url.rstrip("/") + "/.well-known/agent-card.json")
        data.raise_for_status()
        card = data.json()
        if not any(i.get("protocolBinding") == "JSONRPC" and str(i.get("protocolVersion")) == "1.0"
                   for i in card.get("supportedInterfaces", [])):
            raise RuntimeError(f"store {id} is not a A2A v1.0 JSON-RPC service")
        return {"id": id, "url": url, "name": card["name"], "skills": card["skills"]}


def start_demo_shops(n):
    if n < 1 or n > 30:
        raise ValueError("Demo shop count must be 1..30")
    procs, urls = [], []
    log_folder = HERE / "logs" / "challenge-2"
    log_folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        port = unused_port()
        out = open(log_folder / f"seller-{i:02d}.log", "w", encoding="utf-8")
        env = dict(os.environ, SHOP_ID=f"{i:02d}", SHOP_SPECIALTY=["lamp", "chair", "bookshelf"][i % 3])
        proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)],
                                cwd=HERE, env=env, stdout=out, stderr=subprocess.STDOUT)
        procs.append((proc, out))
        urls.append({"id": f"shop-{i:02d}", "url": f"http://127.0.0.1:{port}"})
    try:
        for row in urls:
            for _ in range(40):
                try:
                    fetch_shop(row["url"], row["id"])
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Could not start demo shop " + row["id"])
    except Exception:
        close_shops(procs)
        raise
    return procs, urls


def close_shops(procs):
    for proc, _ in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc, fp in procs:
        try:
            proc.wait(timeout=7)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        fp.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", help="JSON array of shop URLs / {id,url} objects")
    parser.add_argument("--shopping-list", default="shopping_list.json")
    parser.add_argument("--total-budget", type=int, required=True)
    parser.add_argument("--spawn-demo-shops", type=int, default=0)
    args = parser.parse_args()
    procs = []
    try:
        if args.spawn_demo_shops:
            procs, directory = start_demo_shops(args.spawn_demo_shops)
        elif args.directory:
            given = json.loads(Path(args.directory).read_text(encoding="utf-8"))
            directory = [{"id": f"shop-{i:02d}", "url": v if isinstance(v, str) else v["url"]}
                         for i, v in enumerate(given)]
        else:
            raise ValueError("Supply --directory or --spawn-demo-shops")
        shopping_list = json.loads((HERE / args.shopping_list).read_text(encoding="utf-8"))
        if not isinstance(shopping_list, list) or not shopping_list:
            raise ValueError("shopping list must be a nonempty JSON array")
        normalized = [str(v["item"]).strip().lower() for v in shopping_list]
        if len(set(normalized)) != len(normalized):
            raise ValueError("A duplicate item appears on shopping list")
        if args.total_budget < 0:
            raise ValueError("total budget must be >= 0")
        ceilings = [int(v["max_price"]) for v in shopping_list]
        if min(ceilings) < 0 or sum(ceilings) > args.total_budget:
            raise ValueError("Sum of per-item reserved ceilings must be <= total budget")

        with ThreadPoolExecutor(max_workers=12) as pool:
            cards = list(pool.map(lambda x: fetch_shop(x["url"], x["id"]), directory))
        print(f"[multi] discovered {len(cards)} Agent Cards; shared budget={args.total_budget}", flush=True)
        candidates = list(cards)
        jobs = []
        for item, ceiling in zip(normalized, ceilings):
            # Retrieve a small shortlist from ~20 cards before asking the LLM.
            matched = [s for s in candidates if item in json.dumps(s["skills"]).lower()]
            shortlist = (matched or candidates)[:min(7, len(candidates))]
            decision = decide(SELECT_POLICY, {"item": item, "candidates": shortlist}, ["choose"])
            chosen = next((s for s in shortlist if s["id"] == decision.get("sku")), None)
            if chosen is None:
                raise RuntimeError("Model selected a store not included in candidate Agent Cards")
            candidates.remove(chosen)  # distribute concurrent negotiations across shops
            jobs.append((item, ceiling, chosen))
            print(f"[multi] item={item} reserved={ceiling} selected={chosen['id']} name={chosen['name']}", flush=True)
        lock = Lock()
        purchased = set()
        spent = 0
        records = []
        def trade(job):
            item, ceiling, shop = job
            res = buy(shop["url"], item, ceiling)
            return item, ceiling, shop, res
        with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as pool:
            futures = [pool.submit(trade, job) for job in jobs]
            for fut in as_completed(futures):
                item, ceiling, shop, res = fut.result()
                with lock:
                    if res["deal"]:
                        if item in purchased or spent + res["price"] > args.total_budget:
                            raise RuntimeError("Duplicate purchase or total budget exceeded")
                        purchased.add(item)
                        spent += res["price"]
                    records.append({"item": item, "reserved_ceiling": ceiling,
                                    "store": shop["id"], **res})
                    print(f"[multi] item={item} bought={res['deal']} total_spent={spent}", flush=True)
        result = {"total_budget": args.total_budget, "spent": spent,
                  "all_purchased": purchased == set(normalized), "unique_items": len(purchased) == len(set(purchased)),
                  "records": records, "directory_size": len(cards)}
        out = HERE / "logs" / "challenge-2" / "shopping-results.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[multi] summary bought={len(purchased)}/{len(normalized)} spent={spent}/{args.total_budget}", flush=True)
        if not result["all_purchased"]:
            raise SystemExit("Challenge 2 not fully passed; see shopping-results.json")
    finally:
        if procs:
            close_shops(procs)


if __name__ == "__main__":
    main()
