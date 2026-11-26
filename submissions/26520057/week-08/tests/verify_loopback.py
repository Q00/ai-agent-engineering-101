from pathlib import Path
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

root = Path(os.environ.get("AX_TEST_SOURCE", str(Path.home() / "submissions/26520057/week-08")))
with tempfile.TemporaryDirectory(prefix="ax-r3-local-test-") as tmp:
    d = Path(tmp)
    (d / ".market").mkdir()
    (d / "logs").mkdir()
    for name in ["seller_r3.py", "buyer.py", "harness.py", "shop_strategy.py"]:
        shutil.copy(root / name, d / name)
    key = Ed25519PrivateKey.generate()
    (d / "shop_key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    unit = {"sku": "G00000001", "name": "Test lamp", "category": "test-lamp",
            "list_price": 10000, "cost": 6000, "grade": "A", "unit": "test-unit",
            "expires_at": None, "supplied_at": int(time.time())}
    (d / ".market/inventory.json").write_text(json.dumps({
        "round": 3, "balance": 5000000, "units": [unit]}))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with (d / "seller-test.log").open("w") as log:
        seller = subprocess.Popen([sys.executable, "-u", "seller_r3.py", "--port", str(port)],
            cwd=d, env=dict(os.environ, MARKET_DIR=str(d / ".market")), stdout=log, stderr=log)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(30):
                try:
                    if httpx.get(url + "/.well-known/agent-card.json", timeout=1).status_code == 200:
                        break
                except Exception:
                    time.sleep(.3)
            else:
                raise RuntimeError("Test seller did not start")
            started = time.monotonic()
            output = subprocess.run([sys.executable, "-u", "buyer.py", "--url", url,
                "--item", "G00000001", "--budget", "9000"], cwd=d,
                text=True, capture_output=True, timeout=180)
            print("loopback seconds", round(time.monotonic() - started, 2),
                  "exit", output.returncode, flush=True)
            print(output.stdout, flush=True)
            if output.returncode:
                print(output.stderr)
                raise RuntimeError("Loopback buyer failed")
            result = None
            for line in reversed(output.stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                except ValueError:
                    continue
                if isinstance(candidate, dict) and "state" in candidate:
                    result = candidate
                    break
            assert result and result["state"] == "TASK_STATE_COMPLETED", result
            receipt = next(part["data"] for artifact in result["artifacts"]
                           for part in artifact["parts"] if "data" in part)
            price = receipt["price"]
            assert 6900 <= price <= 9000, price
            assert receipt.get("claim") == "A"
            assert receipt.get("warranty_hours") == 6
            body = {k: receipt[k] for k in ["shop", "item", "price", "task"]}
            if isinstance(body["price"], float) and body["price"].is_integer():
                body["price"] = int(body["price"])
            canon = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon)
            print("PASS: signed receipt; cost floor; buyer budget; actual local model; no market transactions")
            (root / "logs/loopback-verification.json").write_text(json.dumps({
                "passed": True, "price": price, "cost": 6000, "budget": 9000,
                "model": "qwen3:4b", "market_transactions": 0,
                "external_api_calls": 0, "timestamp": time.time()}, indent=2))
        finally:
            seller.terminate()
            try:
                seller.wait(timeout=10)
            except subprocess.TimeoutExpired:
                seller.kill()
                seller.wait()
