"""Real model + HTTP/A2A catalog checks; fixed buyer, independently stated needs.

Does not overwrite official reports. The server runs in this process and closes
in finally, so there is no shell-launched server left behind on Windows.
"""
import asyncio
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket
import sys

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from negotiation.a2a_client import A2AClient, asking_price, receipt
from negotiation.buying import settled_task
from negotiation.catalog import Catalog
from negotiation.config import load_settings
from negotiation.runlog import RunLog
from seller import build


CASES = [
    ("open-books", "Something wooden with open storage for about 40 books, at most 80 cm high.",
     {"category": "bookshelf", "material": "wood", "capacity_books": 40, "height_cm": 80}),
    ("enclosed-ko", "책 60권을 보관할 수 있는 파란색 금속 소재의 문 달린 가구가 필요해. 높이는 최대 60cm, 너비는 최대 40cm야.",
     {"category": "cabinet", "material": "metal", "color": "blue", "capacity_books": 60,
      "height_cm": 60, "width_cm": 40}),
    ("reading-light", "A white plastic light for reading, no taller than 30 cm and at most 14 cm wide.",
     {"category": "lamp", "material": "plastic", "color": "white", "height_cm": 30, "width_cm": 14}),
    ("one-seat", "A black metal place for one person to sit, up to 75 cm tall and 44 cm wide.",
     {"category": "chair", "material": "metal", "color": "black", "height_cm": 75, "width_cm": 44}),
    ("writing-surface", "A brown wooden work surface for writing, at most 75 cm high and 80 cm wide.",
     {"category": "desk", "material": "wood", "color": "brown", "height_cm": 75, "width_cm": 80}),
]


def independent_fit(product, expected):
    """No reuse of model's extracted requirements or production matches()."""
    row = product.public()
    for field, value in expected.items():
        if field == "capacity_books":
            assert row[field] >= value, (field, row[field], value)
        elif field in {"height_cm", "width_cm"}:
            assert row[field] <= value, (field, row[field], value)
        else:
            assert row[field] == value, (field, row[field], value)


def selected_row(task):
    parts = task.get("status", {}).get("message", {}).get("parts", [])
    rows = [p["data"]["selected_item"] for p in parts
            if isinstance(p.get("data"), dict) and "selected_item" in p["data"]]
    return rows[0] if len(rows) == 1 else None


class Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, value):
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)

    def flush(self):
        for stream in self.streams:
            stream.flush()

    def isatty(self):
        return False


async def run(logs):
    settings = load_settings()
    if not settings.api_key:
        raise RuntimeError("api_key_missing")
    inventory = Catalog(ROOT / "catalog/products.jsonl")
    log = RunLog(logs / "client-events.jsonl", settings.api_key)
    checks = []
    manifest_files = ["seller.py", "negotiation/harness.py", "negotiation/catalog.py",
                      "negotiation/catalog_contracts.py", "lab/check_catalog.py", "catalog/products.jsonl"]
    manifest_files += ["prompts/" + role + ".md" for role in ("need", "select", "seller")]
    manifest = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in manifest_files}
    log.record("run_start", model=settings.model, source_sha256=manifest, kind="fixed-buyer-catalog-check")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(128)
    sock.setblocking(False)
    server = uvicorn.Server(uvicorn.Config(build(port, logs / "server", inventory.path), log_level="warning"))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    client = A2AClient(log)

    async def settle(task):
        async with asyncio.timeout(100):
            return await settled_task(client, task, task["id"])

    async def buy(task, expected):
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED", task["status"]
        public = selected_row(task)
        assert public is not None, "No selected product in opening"
        product = inventory.products[public["item_id"]]
        assert public == product.public(), "Published product differs from inventory"
        assert "reserve" not in public
        independent_fit(product, expected)
        ask = asking_price(task)
        assert ask >= product.reserve, (ask, product.reserve)
        task = await settle(await client.send(str(ask), task["id"]))
        data = receipt(task, product.item_id, ask, ask)
        assert data["price"] >= product.reserve
        return {"task_id": task["id"], "item": product.item_id, "price": data["price"],
                "reserve": product.reserve, "expected": expected}

    async def check(name, operation):
        try:
            async with asyncio.timeout(150):
                detail = await operation()
            entry = {"name": name, "passed": True, "detail": detail}
        except Exception as error:
            # Log assertions and safe class names, never arbitrary API/HTTP bodies.
            entry = {"name": name, "passed": False, "error_type": type(error).__name__,
                     "detail": str(error) if isinstance(error, AssertionError) else "operation_failed"}
        checks.append(entry)
        log.record("check", **entry)
        print(f"{'PASS' if entry['passed'] else 'FAIL'} {name}: {json.dumps(entry, ensure_ascii=False)}", flush=True)

    try:
        for _ in range(100):
            if server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(.05)
        assert server.started
        await client.discover(f"http://127.0.0.1:{port}")
        for repeat in (1, 2):
            for name, description, expected in CASES:
                async def operation(description=description, expected=expected):
                    return await buy(await settle(await client.send(description)), expected)
                await check(f"{name}-{repeat}", operation)

        async def clarify():
            task = await settle(await client.send("Something for my room."))
            assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
            assert selected_row(task) is None, "Ambiguous request selected an item"
            task = await settle(await client.send(CASES[2][1], task["id"]))
            return await buy(task, CASES[2][2])
        await check("ambiguous-then-clarify-same-task", clarify)

        async def relative():
            task = await settle(await client.send("Something wooden with open storage for 40 books, not taller than my desk."))
            assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
            assert selected_row(task) is None, "Unknown desk height was guessed"
            task = await settle(await client.send("My desk is 80 cm high; maximum height 80 cm.", task["id"]))
            return await buy(task, CASES[0][2])
        await check("relative-height-needs-number", relative)

        for name, description in (
            ("impossible-size", "Wooden open storage for 40 books, maximum height 20 cm."),
            ("unsupported-material", "A seat made of titanium for one person."),
        ):
            async def operation(description=description):
                task = await settle(await client.send(description))
                assert task["status"]["state"] == "TASK_STATE_REJECTED", task["status"]
                assert not task.get("artifacts"), "Rejected request produced artifacts"
                return {"task_id": task["id"], "state": task["status"]["state"]}
            await check(name, operation)

        async def low_offer():
            task = await settle(await client.send(CASES[0][1]))
            product = inventory.products[selected_row(task)["item_id"]]
            independent_fit(product, CASES[0][2])
            asks = [asking_price(task)]
            for _ in range(2):
                task = await settle(await client.send(str(product.reserve - 1), task["id"]))
                state = task["status"]["state"]
                assert state in {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_REJECTED"}, state
                assert not task.get("artifacts"), "Low offer produced a sale"
                if state == "TASK_STATE_REJECTED":
                    break
                asks.append(asking_price(task))
            assert min(asks) >= product.reserve
            if task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED":
                task = await settle(await client.cancel(task["id"]))
            return {"task_id": task["id"], "item": product.item_id, "reserve": product.reserve,
                    "offers": [product.reserve - 1], "asks": asks, "final_state": task["status"]["state"]}
        await check("actual-product-reserve-no-sale", low_offer)
    finally:
        await client.close()
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        sock.close()
        log.record("server_closed", port=port)
    passed = len(checks) == 15 and all(check["passed"] for check in checks)
    summary = {"kind": "challenge-1-real-model-fixed-buyer", "passed": passed,
               "model": settings.model, "catalog_size": len(inventory.products), "checks": checks,
               "source_sha256": manifest}
    with (logs / "summary.json").open("x", encoding="utf-8") as output:
        json.dump(summary, output, ensure_ascii=False, indent=2)
    print(f"Challenge 1: {sum(c['passed'] for c in checks)}/{len(checks)}; logs: {logs}")
    return 0 if passed else 1


def main():
    logs = ROOT / "logs/challenge-1" / datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S.%fZ")
    logs.mkdir(parents=True, exist_ok=False)
    with (logs / "console.log").open("x", encoding="utf-8") as console:
        with redirect_stdout(Tee(sys.stdout, console)), redirect_stderr(Tee(sys.stderr, console)):
            try:
                return asyncio.run(run(logs))
            except Exception as error:
                # Keep the failed run visible even before checks/summary exist.
                print("Catalog run aborted: " + type(error).__name__, file=sys.stderr)
                with (logs / "failure.json").open("x", encoding="utf-8") as output:
                    json.dump({"passed": False, "error_type": type(error).__name__}, output)
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
