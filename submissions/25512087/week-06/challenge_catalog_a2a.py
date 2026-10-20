"""Run Challenge 1 end-to-end against the 1,200-product A2A seller."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from protocol import A2AClient, last_price, receipt_price, task_state, task_text


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_card(url: str, seconds: int = 30) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if httpx.get(url + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("large-catalog seller did not publish its Agent Card")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        default="something wooden to keep about 40 books, not taller than my desk",
    )
    parser.add_argument("--catalog", type=Path, default=Path("catalog.json"))
    args = parser.parse_args()
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable,
        "seller.py",
        "--port",
        str(port),
        "--name",
        "Large Catalog Shop",
        "--catalog",
        str(args.catalog),
    ]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        wait_card(url)
        client = A2AClient(url)
        try:
            client.discover()
            task = client.send(args.query)
            ask = last_price(task)
            print(
                json.dumps(
                    {
                        "event": "challenge_1_open",
                        "query": args.query,
                        "task_id": task.get("id"),
                        "state": task_state(task),
                        "message": task_text(task),
                        "ask": ask,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if ask is None:
                raise RuntimeError("seller returned no numeric ask")
            task = client.send(str(ask), str(task["id"]))
            print(
                json.dumps(
                    {
                        "event": "challenge_1_result",
                        "state": task_state(task),
                        "price": receipt_price(task),
                        "message": task_text(task),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if task_state(task) != "TASK_STATE_COMPLETED":
                raise RuntimeError("large-catalog trade did not complete")
        finally:
            client.close()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
