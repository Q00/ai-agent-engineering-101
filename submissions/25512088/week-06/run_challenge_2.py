"""Start 20 A2A shops and run the required multi-shop shopping-list demo."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs" / "challenge-2"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    products = [("lamp", 100), ("chair", 180), ("bookshelf", 260)] + [
        (f"decor-{index:02d}", 80 + index * 5) for index in range(17)
    ]
    processes = []
    with tempfile.TemporaryDirectory(prefix="week06-shops-") as temp, \
            (LOG_DIR / "sellers.log").open("w", encoding="utf-8") as seller_log:
        temp_path = Path(temp)
        directory = []
        try:
            for index, (item, reserve) in enumerate(products):
                port = free_port()
                config = {
                    "name": f"Shop {index:02d} {item.title()}",
                    "description": f"Specialist A2A shop for {item}; negotiates one {item} purchase.",
                    "reserve": {item: reserve},
                }
                config_path = temp_path / f"shop-{index:02d}.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, "seller_agent.py", "--port", str(port), "--config", str(config_path)],
                    cwd=ROOT, stdout=seller_log, stderr=subprocess.STDOUT, text=True,
                )
                processes.append(process)
                directory.append({"id": f"shop-{index:02d}", "url": f"http://127.0.0.1:{port}"})

            for shop in directory:
                for _ in range(60):
                    try:
                        if httpx.get(shop["url"] + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"shop did not start: {shop}")

            shopping = [
                {"item": "lamp", "limit": 120},
                {"item": "chair", "limit": 200},
                {"item": "bookshelf", "limit": 280},
            ]
            directory_path = LOG_DIR / "directory.json"
            shopping_path = LOG_DIR / "shopping-list.json"
            result_path = LOG_DIR / "result.json"
            directory_path.write_text(json.dumps(directory, indent=2) + "\n", encoding="utf-8")
            shopping_path.write_text(json.dumps(shopping, indent=2) + "\n", encoding="utf-8")
            command = [
                sys.executable, "multi_shop_buyer.py",
                "--directory", str(directory_path),
                "--shopping-list", str(shopping_path),
                "--budget", "570", "--output", str(result_path),
            ]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600, check=False)
            (LOG_DIR / "buyer.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            print(result.stdout, end="")
            return result.returncode
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
