"""Exercise the A2A catalog seller with a description instead of an item name."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

from catalog import Catalog


ROOT = Path(__file__).resolve().parent
QUERY = "something wooden to keep about 40 books, not taller than my desk"
BUDGET = 190


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run() -> dict:
    log_root = ROOT / "logs" / "challenge-1"
    log_root.mkdir(parents=True, exist_ok=True)
    number = 1
    while (log_root / f"attempt-{number:02d}").exists():
        number += 1
    logs = log_root / f"attempt-{number:02d}"
    logs.mkdir()
    port = free_port()
    with (logs / "seller.log").open("w", encoding="utf-8") as seller_log:
        process = subprocess.Popen(
            [sys.executable, "seller.py", "--port", str(port), "--catalog", "catalog.json"],
            cwd=ROOT, stdout=seller_log, stderr=subprocess.STDOUT, text=True,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(60):
                try:
                    if httpx.get(base + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            else:
                raise RuntimeError("catalog seller did not serve an Agent Card")
            buyer = subprocess.run(
                [sys.executable, "buyer.py", "--url", base, "--item", QUERY, "--budget", str(BUDGET)],
                cwd=ROOT, text=True, capture_output=True, timeout=300,
            )
            (logs / "buyer.log").write_text(buyer.stdout + buyer.stderr, encoding="utf-8")
            if buyer.returncode:
                raise RuntimeError(f"buyer exited {buyer.returncode}; see {logs / 'buyer.log'}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    events = [json.loads(line) for line in (logs / "buyer.log").read_text().splitlines() if line.startswith("{")]
    result = next(event for event in reversed(events) if event.get("event") == "buyer_result")
    receipt = next(
        part["data"] for artifact in result["artifacts"] for part in artifact["parts"] if "data" in part
    )
    product = Catalog(ROOT / "catalog.json").by_id[receipt["product_id"]]
    passed = (
        result["state"] == "TASK_STATE_COMPLETED"
        and product["category"] == "bookshelf"
        and product["material"] == "wood"
        and product["capacity_books"] == 40
        and product["height_cm"] <= 75
        and product["reserve"] <= receipt["price"] <= BUDGET
    )
    summary = {
        "passed": passed, "catalog_size": 2346, "query": QUERY,
        "product_id": product["id"], "reserve": product["reserve"],
        "price": receipt["price"], "budget": BUDGET,
        "logs": str(logs.relative_to(ROOT)),
    }
    (logs / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if not passed:
        raise SystemExit(1)
    return summary


if __name__ == "__main__":
    run()
