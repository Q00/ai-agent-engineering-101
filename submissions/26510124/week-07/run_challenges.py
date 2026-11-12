"""Reproduce the required challenges with real Claude calls and immutable logs.

    .venv/bin/python run_challenges.py --challenge 1
    .venv/bin/python run_challenges.py --challenge 2

Challenge 1 uses a scripted test customer. Challenge 2 uses our model buyer.
No model is mocked here. Each run starts and cleans up its own local servers.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import traceback
import uuid

import httpx

from buyer_agent import Buyer, VERSION_HEADERS

ROOT = Path(__file__).resolve().parent


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


class Tee:
    def __init__(self, terminal, file):
        self.terminal, self.file = terminal, file

    def write(self, text):
        self.terminal.write(text)
        self.file.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.file.flush()


class Shops:
    def __init__(self, logdir):
        self.logdir = logdir
        self.processes = []
        self.files = ExitStack()
        self.ports = set()

    def __enter__(self):
        return self

    def start(self, shop_id, **options):
        while True:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            if port not in self.ports:
                self.ports.add(port)
                break
        cfg = {"name": shop_id, "port": port, **options}
        config = self.logdir / f"{shop_id}-config.json"
        save(config, cfg)
        log = self.files.enter_context((self.logdir / f"{shop_id}-server.log").open("x"))
        env = {**os.environ, "WEEK06_LOG_DIR": str(self.logdir / "harness")}
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "catalog_seller.py"), "--config", str(config)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        self.processes.append((shop_id, process))
        return {"id": shop_id, "url": f"http://127.0.0.1:{port}"}

    def ready(self, shops):
        cards = {}
        deadline = time.monotonic() + 60
        with httpx.Client(trust_env=False) as client:
            while len(cards) < len(shops):
                for shop_id, process in self.processes:
                    if process.poll() is not None:
                        raise RuntimeError(f"{shop_id} exited: see its server log")
                for shop in shops:
                    if shop["id"] in cards:
                        continue
                    try:
                        r = client.get(shop["url"] + "/.well-known/agent-card.json",
                                       headers=VERSION_HEADERS, timeout=1)
                        r.raise_for_status()
                        cards[shop["id"]] = r.json()
                    except (httpx.HTTPError, ValueError):
                        pass
                if len(cards) == len(shops):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Shop startup deadline exceeded")
                time.sleep(0.1)
        save(self.logdir / "agent-cards.json", cards)
        print(f"READY: {len(cards)} live Agent Cards", flush=True)

    def __exit__(self, *exc):
        for _, process in self.processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for _, process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        self.files.close()
        print("CLEANUP: all demonstration servers stopped", flush=True)


def data_parts(task):
    return [p["data"] for p in (task.get("status", {}).get("message") or {}).get("parts", [])
            if isinstance(p, dict) and isinstance(p.get("data"), dict)]


def satisfies(product, constraints):
    for field, expected in constraints.items():
        if field.endswith("_max"):
            actual = product.get(field[:-4])
            if type(actual) not in (int, float) or actual > expected:
                return False
        elif field.endswith("_min"):
            actual = product.get(field[:-4])
            if type(actual) not in (int, float) or actual < expected:
                return False
        elif product.get(field) != expected:
            return False
    return True


def challenge_one(logdir, fixture):
    # This checker knows the catalog reserve; it never supplies it to a buyer model.
    catalog = json.loads((ROOT / "data/catalog.json").read_text())
    products = catalog if isinstance(catalog, list) else catalog["items"]
    by_id = {p["id"]: p for p in products}
    results = []
    with Shops(logdir) as servers:
        shop = servers.start("catalog-shop")
        servers.ready([shop])
        for case in fixture["items"] + fixture["unavailable_cases"]:
            unavailable = case in fixture["unavailable_cases"]
            with httpx.Client(trust_env=False) as client:
                buyer = Buyer(client, url=shop["url"], item=case["id"], budget=10000)
                try:
                    buyer.discover()
                    # The seller receives only this description, no SKU or filters.
                    task = buyer.send(case["description"])
                    quotes = [p for p in data_parts(task) if "product" in p]
                    if unavailable:
                        state = task["status"]["state"]
                        if quotes or state not in {"TASK_STATE_REJECTED", "TASK_STATE_INPUT_REQUIRED"}:
                            raise AssertionError(f"Unavailable request unexpectedly produced a product: {task}")
                        result = {"case": case["id"], "passed": True, "state": state,
                                  "no_wrong_item_sold": True}
                    else:
                        if len(quotes) != 1:
                            raise AssertionError(f"Expected one selected product: {task}")
                        quote = quotes[0]
                        product = quote["product"]
                        if not satisfies(product, case["constraints"]):
                            raise AssertionError(f"Wrong product for {case['id']}: {product}")
                        source = by_id[product["id"]]
                        if any(product.get(k) != v for k, v in source.items() if k != "reserve"):
                            raise AssertionError("Quoted attributes do not match the committed catalog")
                        asks = []
                        for _ in range(3):
                            ask = quote["ask"]
                            if type(ask) not in (int, float) or ask < source["reserve"]:
                                raise AssertionError("Ask below selected product reserve")
                            asks.append(ask)
                            # Test customer accepts the model's current ask verbatim.
                            task = buyer.send(str(int(ask)))
                            if task["status"]["state"] == "TASK_STATE_COMPLETED":
                                break
                            parts = [p for p in data_parts(task) if "product" in p]
                            if len(parts) != 1 or parts[0]["product"]["id"] != product["id"]:
                                raise AssertionError("Seller changed product or failed to close")
                            quote = parts[0]
                        receipts = [p["data"] for a in task.get("artifacts", []) for p in a.get("parts", [])
                                    if isinstance(p.get("data"), dict) and "price" in p["data"]]
                        if task["status"]["state"] != "TASK_STATE_COMPLETED" or len(receipts) != 1:
                            raise AssertionError("No single completed receipt")
                        receipt = receipts[0]
                        if (receipt.get("item") != product["id"] or receipt.get("product") != product
                                or receipt["price"] != asks[-1] or receipt["price"] < source["reserve"]):
                            raise AssertionError("Receipt product/price does not match the approved quote")
                        result = {"case": case["id"], "passed": True, "product": product,
                                  "asks": asks, "receipt": receipt, "reserve": source["reserve"]}
                    results.append(result)
                    print("CASE RESULT:", json.dumps(result), flush=True)
                finally:
                    if buyer.task_id and not buyer.terminal:
                        buyer.cancel(cleanup=True)
    return {"challenge": 1, "passed": len(results) == 4 and all(r["passed"] for r in results),
            "catalog_count": len(products), "results": results}


def challenge_two(logdir, fixture, budget):
    with Shops(logdir) as servers:
        shops = []
        for category in ["lamp", "chair", "bookshelf", "table"]:
            for variant in range(5):
                shops.append(servers.start(
                    f"{category}-shop-{variant + 1}", categories=[category],
                    reserve_offset=variant * 5, inventory_mod=5, inventory_remainder=variant,
                ))
        servers.ready(shops)
        directory = logdir / "directory.json"
        shopping_list = logdir / "shopping-list.json"
        save(directory, {"shops": shops})
        save(shopping_list, {"items": fixture["items"]})
        output = logdir / "shopping-result.json"
        command = [sys.executable, "-u", str(ROOT / "shopping_buyer.py"),
                   "--directory", str(directory), "--list", str(shopping_list),
                   "--budget", str(budget), "--output", str(output)]
        print("BUYER COMMAND:", json.dumps(command), flush=True)
        with (logdir / "buyer.log").open("x") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       env={**os.environ, "WEEK06_LOG_DIR": str(logdir / "harness")},
                                       start_new_session=True)
            try:
                code = process.wait(timeout=300)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
        result = json.loads(output.read_text()) if output.exists() else {"passed": False, "error": "No result file"}
        print("SHOPPING RESULT:", json.dumps(result), flush=True)
        if code != 0 or not result.get("passed"):
            raise AssertionError(f"Shopping buyer failed (exit {code}); see buyer.log and shopping-result.json")
        if result.get("cards_read") != 20 or result.get("peak_distinct_shops", 0) < 2:
            raise AssertionError("The demonstration must read 20 cards and negotiate with multiple shops concurrently")
        expected = {item["id"]: item for item in fixture["items"]}
        receipts = result.get("receipts", [])
        actual_ids = [receipt.get("shopping_item_id") for receipt in receipts]
        if len(actual_ids) != len(expected) or set(actual_ids) != set(expected):
            raise AssertionError("Missing or duplicate shopping-list receipt")
        products = {p["id"]: p for p in json.loads((ROOT / "data/catalog.json").read_text())}
        for receipt in receipts:
            product = receipt["product"]
            if not satisfies(product, expected[receipt["shopping_item_id"]]["constraints"]):
                raise AssertionError("Purchased product does not satisfy the original shopping list")
            shop_cfg = json.loads((logdir / f"{receipt['shop_id']}-config.json").read_text())
            source = products[product["id"]]
            reserve = source["reserve"] + shop_cfg["reserve_offset"]
            if receipt["price"] < reserve:
                raise AssertionError("A shop sold below its own catalog reserve")
        if sum(receipt["price"] for receipt in receipts) > budget:
            raise AssertionError("Aggregate purchases exceeded the total budget")
        events = [json.loads(line) for line in (logdir / "buyer.log").read_text().splitlines() if line.startswith('{')]
        contexts = [event for event in events if event.get("event") == "compact_context"]
        summary_reused = any(event["context"].get("summary") for event in contexts)
        if any(event["summary_chars"] > 800 or event["recent_turns"] > 2 for event in contexts):
            raise AssertionError("Buyer context exceeded its summary/turn bounds")
        print("INDEPENDENT CHECK:", json.dumps({"correct_items": True, "within_budget": True,
              "no_duplicates": True, "reserves_held": True, "summary_reused": summary_reused}), flush=True)
        return {"challenge": 2, "passed": True, "directory_size": len(shops),
                "summary_reused": summary_reused, "result": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", choices=["1", "2", "all"], default="all")
    parser.add_argument("--budget", type=int, default=450)
    args = parser.parse_args()
    fixture = json.loads((ROOT / "data/shopping_list.json").read_text())
    overall = True
    for challenge in ([1, 2] if args.challenge == "all" else [int(args.challenge)]):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        logdir = ROOT / "logs" / f"challenge-{challenge}" / stamp
        logdir.mkdir(parents=True)
        original_stdout, original_stderr = sys.stdout, sys.stderr
        with (logdir / "console.log").open("x") as console:
            sys.stdout, sys.stderr = Tee(original_stdout, console), Tee(original_stderr, console)
            try:
                print(f"START: challenge={challenge}, logdir={logdir.relative_to(ROOT)}", flush=True)
                save(logdir / "input.json", {"challenge": challenge, "fixture": fixture, "budget": args.budget})
                result = challenge_one(logdir, fixture) if challenge == 1 else challenge_two(logdir, fixture, args.budget)
            except Exception as exc:
                traceback.print_exc()
                result = {"challenge": challenge, "passed": False, "error": str(exc)}
            finally:
                sys.stdout, sys.stderr = original_stdout, original_stderr
            save(logdir / "result.json", result)
        print(f"RESULT: challenge {challenge}: {'PASS' if result['passed'] else 'FAIL'} ({logdir.relative_to(ROOT)})", flush=True)
        overall = overall and result["passed"]
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
