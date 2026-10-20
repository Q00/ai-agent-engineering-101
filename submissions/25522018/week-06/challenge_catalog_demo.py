"""Challenge 1: live A2A purchase using natural-language catalog matching.

Creates genuine evidence under logs/challenge-1 when run with a valid Groq key.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from buyer import buy
from catalog import Catalog

HERE = Path(__file__).resolve().parent
QUERY = "I want something wooden to keep about 40 books, not taller than my desk"


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    catalog = Catalog()
    if len(catalog.items) < 1000:
        raise RuntimeError("Challenge 1 requires a large catalog")
    logdir = HERE / "logs" / "challenge-1"
    logdir.mkdir(parents=True, exist_ok=True)
    with (logdir / "retrieval.log").open("w", encoding="utf-8") as fp:
        print(f"catalog_size={len(catalog.items)}\nquery={QUERY}", file=fp)
        for item in catalog.search(QUERY, top_k=5):
            print(json.dumps(item, ensure_ascii=False), file=fp)
    port = unused_port()
    with (logdir / "seller-console.log").open("w", encoding="utf-8") as seller_fp:
        server = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)],
                                  cwd=HERE, env=os.environ.copy(), stdout=seller_fp, stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(30):
                try:
                    resp = httpx.get(url + "/.well-known/agent-card.json", timeout=1.5)
                    if resp.status_code == 200:
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                raise RuntimeError("Catalog seller did not start; see seller-console.log")
            outcome = buy(url, QUERY, 420)
            (logdir / "result.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
            if not outcome["deal"]:
                raise RuntimeError("Description-driven negotiation ended without a sale")
            print("[challenge-1] PASS: large-catalog natural language purchase completed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=7)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
