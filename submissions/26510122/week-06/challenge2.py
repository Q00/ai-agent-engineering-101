"""Serve twenty Agent Cards and run three concurrent A2A purchases."""

from __future__ import annotations

from contextlib import redirect_stdout
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import httpx
import uvicorn

from seller import build


ROOT = Path(__file__).resolve().parent
BASE_RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run() -> dict:
    root = ROOT / "logs" / "challenge-2"
    root.mkdir(parents=True, exist_ok=True)
    number = 1
    while (root / f"attempt-{number:02d}").exists():
        number += 1
    logs = root / f"attempt-{number:02d}"
    logs.mkdir()
    profiles = []
    ports = []
    for index in range(20):
        item = ("lamp", "chair", "bookshelf")[index % 3]
        reserve = BASE_RESERVE[item] + (index // 3) * 8
        profiles.append({
            "name": f"Shop {index + 1:02d}", "reserve": {item: reserve},
            "description": f"Sells {item}; indicative minimum price {reserve}. A2A price negotiation available.",
        })
        ports.append(free_port())
    directory = [f"http://127.0.0.1:{port}" for port in ports]
    shopping_list = {"items": ["lamp", "chair", "bookshelf"], "total_budget": 650}
    (logs / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")
    (logs / "directory.json").write_text(json.dumps(directory, indent=2) + "\n", encoding="utf-8")
    (logs / "shopping-list.json").write_text(json.dumps(shopping_list, indent=2) + "\n", encoding="utf-8")

    servers = []
    with (logs / "seller.log").open("w", encoding="utf-8") as seller_log, redirect_stdout(seller_log):
        try:
            for port, profile in zip(ports, profiles):
                server = uvicorn.Server(uvicorn.Config(
                    build(port, profile), host="127.0.0.1", port=port,
                    log_level="error", lifespan="off",
                ))
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()
                servers.append((server, thread))
            with httpx.Client(timeout=2) as client:
                for url in directory:
                    for _ in range(40):
                        try:
                            if client.get(url + "/.well-known/agent-card.json").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.25)
                    else:
                        raise RuntimeError(f"shop card unavailable: {url}")
            buyer = subprocess.run(
                [sys.executable, "multi_buyer.py", "--directory", str(logs / "directory.json"),
                 "--shopping-list", str(logs / "shopping-list.json")],
                cwd=ROOT, capture_output=True, text=True, timeout=300,
            )
            (logs / "buyer.log").write_text(buyer.stdout + buyer.stderr, encoding="utf-8")
        finally:
            for server, _ in servers:
                server.should_exit = True
            for _, thread in servers:
                thread.join(timeout=5)
    if buyer.returncode:
        raise RuntimeError(f"multi-buyer exited {buyer.returncode}; see {logs / 'buyer.log'}")
    events = [json.loads(line) for line in (logs / "buyer.log").read_text().splitlines() if line.startswith("{")]
    result = next(event for event in reversed(events) if event.get("event") == "shopping_result")
    summary = {key: value for key, value in result.items() if key != "event"}
    summary["directory_size"] = len(directory)
    summary["logs"] = str(logs.relative_to(ROOT))
    (logs / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if not summary["passed"]:
        raise SystemExit(1)
    return summary


if __name__ == "__main__":
    run()
