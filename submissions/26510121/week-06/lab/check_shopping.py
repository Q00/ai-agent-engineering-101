"""Real model shopping against 20 live model-driven A2A sellers.

The verifier owns private inventories; the buyer subprocess receives only a
public card directory and shopping list. No official report is overwritten.
"""
import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from negotiation.catalog import Catalog
from negotiation.config import load_settings
from negotiation.runlog import RunLog
from seller import build


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return False


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def buyer(directory_path, list_path, logs):
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-u", str(ROOT / "cart_buyer.py"), "--directory", str(directory_path),
               "--shopping-list", str(list_path), "--log-dir", str(logs)]
    process = await asyncio.create_subprocess_exec(*command, cwd=ROOT, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if os.name == "nt" else 0)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    with (logs / "stdout.log").open("xb") as output:
        output.write(stdout)
    with (logs / "stderr.log").open("xb") as output:
        output.write(stderr)
    return process.returncode, json.loads(stdout.decode("utf-8"))


def audit(shopping, result, buyer_dir, shops, previous_receipts):
    expected = {row["item_id"] for row in shopping["items"]}
    total = shopping["total_budget"]
    assert result["outcome"] == "purchased", result.get("code", result.get("outcome"))
    assert len(result["lines"]) == len(expected)
    assert all(line["state"] == "purchased" for line in result["lines"])
    assert {line["receipt"]["item"] for line in result["lines"]} == expected
    all_sales = []
    for shop_id, shop in shops.items():
        source = events(shop["events"])
        new_sales = [event for event in source if event["event"] == "receipt"
                     and event["task_id"] not in previous_receipts]
        for sale in new_sales:
            row = shop["catalog"].products[sale["receipt"]["item"]]
            assert sale["receipt"]["price"] >= row.reserve
            assert row.material == "wood" and row.color == "white"
            all_sales.append({"shop_id": shop_id, **sale})
        for event in source:
            if event["event"] == "seller_decision" and event["decision"]["price"] is not None:
                product = shop["catalog"].products[event["decision"]["item_id"]]
                assert event["decision"]["price"] >= product.reserve
    assert len(all_sales) == len(expected), "Missing or duplicate actual sale"
    assert len({sale["receipt"]["item"] for sale in all_sales}) == len(expected)
    sales_by_task = {sale["task_id"]: sale for sale in all_sales}
    for line in result["lines"]:
        sale = sales_by_task[line["task_id"]]
        assert sale["shop_id"] == line["shop_id"]
        assert sale["receipt"]["item"] == line["item_id"]
        assert sale["receipt"]["price"] == line["receipt"]["price"] <= line["cap"]
        assert line["task_id"] and len(line["summary"]) <= 600 and len(line["recent_turns"]) <= 4
    spent = sum(sale["receipt"]["price"] for sale in all_sales)
    assert spent == result["spent"] <= total and result["held"] == 0
    entries = events(buyer_dir / "cart-events.jsonl")
    resolved = next(event for event in entries if event["event"] == "directory_resolved")
    assert resolved["requested"] == resolved["available"] == 20
    snapshots = [event["ledger"] for event in entries if "ledger" in event]
    assert all(row["spent"] + row["held"] <= total and row["available"] >= 0 for row in snapshots)
    peak = max(sum(line["state"] == "active" for line in row["lines"]) for row in snapshots)
    assert 2 <= peak <= 4, "No concurrent negotiations observed"
    request_count, memory_calls, max_cards = 0, 0, 0
    for path in buyer_dir.glob("model-*.jsonl"):
        for event in events(path):
            if event["event"] != "model_request":
                continue
            request_count += 1
            if event["role"] == "buyer":
                observation = event["input"]["observation"]
                assert len(observation["memory_untrusted"]["recent_turns"]) <= 4
                assert len(observation["memory_untrusted"]["summary"]) <= 600
                assert "cards_untrusted" not in observation
                memory_calls += bool(observation["memory_untrusted"]["summary"])
            if event["role"] == "cart":
                max_cards = max(max_cards, len(event["input"]["observation"]["cards_untrusted"]))
                assert all(len(line["shops"]) <= 4 for line in event["input"]["trusted_context"]["lines"])
                assert "reserve" not in json.dumps(event["input"]["observation"]["cards_untrusted"])
    previous_receipts.update(sales_by_task)
    return {"spent": spent, "total_budget": total, "actual_sales": len(all_sales), "peak_concurrent": peak,
            "model_requests": request_count, "buyer_calls_using_summary": memory_calls,
            "max_planner_cards": max_cards,
            "purchases": [{"line_id": line["line_id"], "shop_id": line["shop_id"],
                           "price": line["receipt"]["price"], "cap": line["cap"]} for line in result["lines"]]}


async def run(logs, repeats):
    settings = load_settings()
    if not settings.api_key:
        raise RuntimeError("api_key_missing")
    log = RunLog(logs / "verifier-events.jsonl", settings.api_key)
    manifest_files = ["seller.py", "cart_buyer.py", "negotiation/buying.py", "negotiation/harness.py",
                      "negotiation/cart_contracts.py", "negotiation/cart_state.py", "negotiation/shopping.py",
                      "lab/check_shopping.py", "shopping/shopping-list.json"]
    manifest_files += [p.relative_to(ROOT).as_posix() for p in (ROOT / "shopping/shops").glob("*")]
    manifest_files += ["prompts/" + role + ".md" for role in ("cart", "buyer", "seller", "need", "select")]
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in manifest_files}
    log.record("start", source_sha256=manifest, model=settings.model)
    config = json.loads((ROOT / "shopping/shops.json").read_text(encoding="utf-8"))
    shopping = json.loads((ROOT / "shopping/shopping-list.json").read_text(encoding="utf-8"))
    list_path = logs / "shopping-list.json"
    list_path.write_bytes((ROOT / "shopping/shopping-list.json").read_bytes())
    servers, sockets, tasks, shops, checks = [], [], [], {}, []
    previous_receipts = set()
    try:
        for entry in config["shops"]:
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            sock.setblocking(False)
            port = sock.getsockname()[1]
            profile = json.loads((ROOT / "shopping" / entry["profile"]).read_text(encoding="utf-8"))
            catalog = Catalog(ROOT / "shopping" / entry["catalog"])
            shop_logs = logs / "sellers" / entry["shop_id"]
            app = build(port, shop_logs, catalog.path, profile)
            server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False,
                                                   timeout_graceful_shutdown=3))
            servers.append(server)
            sockets.append(sock)
            tasks.append(asyncio.create_task(server.serve(sockets=[sock])))
            shops[entry["shop_id"]] = {"url": f"http://127.0.0.1:{port}", "catalog": catalog,
                                      "events": shop_logs / "server-events.jsonl"}
        for _ in range(200):
            if all(server.started for server in servers):
                break
            if any(task.done() for task in tasks):
                raise RuntimeError("shop_server_stopped_during_startup")
            await asyncio.sleep(.05)
        assert len(servers) == 20 and all(server.started for server in servers)
        async with httpx.AsyncClient(headers={"A2A-Version": "1.0"}, timeout=10, trust_env=False) as http:
            async def card_entry(shop_id, shop):
                response = await http.get(shop["url"] + "/.well-known/agent-card.json")
                response.raise_for_status()
                return {"shop_id": shop_id, "url": shop["url"], "card": response.json()}
            entries = await asyncio.gather(*(card_entry(key, shop) for key, shop in shops.items()))
        directory_path = logs / "directory.json"
        with directory_path.open("x", encoding="utf-8") as output:
            json.dump({"shops": entries}, output, ensure_ascii=False, indent=2)
        print("Started 20 model-driven shops; public card directory saved.", flush=True)
        for repeat in range(1, repeats + 1):
            buyer_dir = logs / f"buyer-{repeat}"
            try:
                code, result = await buyer(directory_path, list_path, buyer_dir)
                assert code == 0, result.get("code", result.get("outcome"))
                detail = audit(shopping, result, buyer_dir, shops, previous_receipts)
                check = {"name": f"shopping-{repeat}", "passed": True, "detail": detail}
            except Exception as error:
                check = {"name": f"shopping-{repeat}", "passed": False, "error_type": type(error).__name__,
                         "detail": str(error) if isinstance(error, AssertionError) else "operation_failed"}
            checks.append(check)
            log.record("check", **check)
            print(f"{'PASS' if check['passed'] else 'FAIL'} {json.dumps(check, ensure_ascii=False)}", flush=True)
            if not check["passed"]:
                break
    finally:
        for server in servers:
            server.should_exit = True
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
        ports = [sock.getsockname()[1] for sock in sockets if sock.fileno() != -1]
        for sock in sockets:
            sock.close()
        log.record("all_shops_closed", count=len(servers), ports=ports)
    passed = len(checks) == repeats and all(check["passed"] for check in checks)
    with (logs / "summary.json").open("x", encoding="utf-8") as output:
        json.dump({"kind": "challenge-2-real-model-many-shops", "passed": passed, "requested_repeats": repeats,
                   "model": settings.model, "checks": checks, "source_sha256": manifest}, output,
                  ensure_ascii=False, indent=2)
    print(f"Challenge 2: {sum(c['passed'] for c in checks)}/{repeats}; logs: {logs}")
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 3:
        parser.error("repeats must be 1..3")
    logs = ROOT / "logs/challenge-2" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    logs.mkdir(parents=True, exist_ok=False)
    with (logs / "console.log").open("x", encoding="utf-8") as console:
        with redirect_stdout(Tee(sys.stdout, console)), redirect_stderr(Tee(sys.stderr, console)):
            try:
                return asyncio.run(run(logs, args.repeats))
            except Exception as error:
                print("Shopping verifier aborted: " + type(error).__name__, file=sys.stderr)
                with (logs / "failure.json").open("x", encoding="utf-8") as output:
                    json.dump({"passed": False, "error_type": type(error).__name__}, output)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
