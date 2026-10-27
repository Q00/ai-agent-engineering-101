"""Exercise the public guarantee against real local A2A receipts, without a harness."""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE.parents[2] / "scripts"))
import market_test_week07 as kit


def main():
    output = BASE / "logs" / datetime.now(timezone.utc).strftime("quality-check-%Y%m%dT%H%M%S%fZ")
    output.mkdir()
    port = kit.free_port()
    origin = f"http://127.0.0.1:{port}"
    env = dict(os.environ, SELLER_MEMORY=str(BASE / ".market/quality-check-memory.jsonl"))
    with (output / "seller.log").open("x") as log:
        proc = subprocess.Popen([sys.executable, "seller.py", "--port", str(port)], cwd=BASE,
                                env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            card = kit.wait_card(origin)
            assert card is not None, "Seller did not start"
            assert len(card["description"]) <= 300, "Market truncates descriptions at 300 characters"
            assert "/.well-known/quality-guarantee.json" in card["description"][:300]
            response = httpx.get(origin + "/.well-known/quality-guarantee.json")
            response.raise_for_status()
            guarantee = response.json()
            public = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(guarantee["verification"]["public_key_base64"]))
            assert public.public_bytes_raw() == kit.shop_key(BASE).public_key().public_bytes_raw()
            results = []
            for item, price in guarantee["final_prices"].items():
                # An excessive offer must first be corrected to the advertised price.
                task, error = kit.send(origin + "/", f"I want to buy a {item} for {price + 50}")
                assert not error and kit.state(task) == "TASK_STATE_INPUT_REQUIRED"
                assert kit.last_number(task) == price
                assert task["status"]["message"]["metadata"]["ask"] == price
                task_id = task["id"]
                task, error = kit.send(origin + "/", "I accept", task_id)
                assert not error and kit.state(task) == "TASK_STATE_COMPLETED"
                receipt = kit.receipt_of(task)
                assert receipt["item"] == item and receipt["price"] == price
                assert receipt["shop"] == "psyoung82" and receipt["task"] == task_id
                assert kit.sig_ok(public, receipt)
                assert not kit.sig_ok(public, {**receipt, "price": price + 1})
                assert not kit.sig_ok(public, {**receipt, "item": "different-item"})
                assert not kit.sig_ok(public, {**receipt, "task": "different-task"})
                results.append({"item": item, "price": price, "receipt": receipt,
                                "signature_verified": True, "tampering_rejected": True})
            (output / "verification.json").write_text(
                json.dumps({"card": card, "guarantee": guarantee, "purchases": results,
                            "passed": True}, ensure_ascii=False, indent=2) + "\n")
            print("PASS: all three items match advertised prices and accepted quotes; "
                  "signatures verify; altered item, price and task fail verification")
            print(f"Evidence: {output}")
        finally:
            kit.stop(proc)


if __name__ == "__main__":
    main()
