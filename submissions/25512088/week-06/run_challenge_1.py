"""Run the required large-catalog seller demonstration and preserve its logs."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from buyer_agent import send, state, status_text


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs" / "challenge-1"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with (LOG_DIR / "seller.log").open("w", encoding="utf-8") as seller_log:
        process = subprocess.Popen(
            [sys.executable, "seller_agent.py", "--port", str(port), "--catalog", "catalog.json"],
            cwd=ROOT, stdout=seller_log, stderr=subprocess.STDOUT, text=True,
        )
        try:
            card = None
            for _ in range(60):
                try:
                    response = httpx.get(base + "/.well-known/agent-card.json", timeout=2)
                    if response.status_code == 200:
                        card = response.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            if card is None:
                raise RuntimeError("large-catalog seller did not start")
            query = "I need something wooden to keep about 40 books, not taller than my desk."
            task, error = send(card["supportedInterfaces"][0]["url"], query)
            if error or not task:
                raise RuntimeError(error)
            first = {"state": state(task), "message": status_text(task), "task_id": task["id"]}
            task, error = send(card["supportedInterfaces"][0]["url"], "260", task["id"])
            if error or not task:
                raise RuntimeError(error)
            record = {
                "catalog_size": 2003, "query": query, "first": first,
                "final_state": state(task), "final_message": status_text(task),
                "artifacts": task.get("artifacts", []),
            }
            (LOG_DIR / "client.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            passed = (
                state(task) == "TASK_STATE_COMPLETED"
                and "Compact Oak Bookshelf" in first["message"]
                and any(
                    part.get("data", {}).get("price", 0) >= 260
                    for artifact in task.get("artifacts", [])
                    for part in artifact.get("parts", [])
                    if isinstance(part.get("data"), dict)
                )
            )
            print(f"challenge-1 {'PASS' if passed else 'FAIL'}: {json.dumps(record, ensure_ascii=False)}")
            return 0 if passed else 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
