from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = Path(__file__).parents[4]
EXPERIMENTS = Path(__file__).parent
if str(REPO) not in sys.path: sys.path.insert(0, str(REPO))

from operational_candidate.buyer import BOriginalClient, CandidateBuyerError, list_price_from_catalog
from operational_candidate.ledger import LedgerError, TradeLedger
from operational_candidate.restock_dry_run import preview as restock_preview


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); return sock.getsockname()[1]


def test_key(path: Path) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return key


class LocalA2AE2E(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        fixtures = EXPERIMENTS / "operational_candidate" / "fixtures"
        for name in ("catalog.json", "inventory.json", "demand.json", "order.json", "shops.json"):
            (self.root / name).write_bytes((fixtures / name).read_bytes())
        self.key_path = self.root / "test-shop-key.pem"; self.key = test_key(self.key_path)
        self.ledger = self.root / "test-ledger.json"; self.process = None

    def tearDown(self):
        self.stop_server(); self.temp.cleanup()

    def start_server(self):
        port = free_port()
        command = [sys.executable, "-m", "operational_candidate.seller", "--port", str(port),
                   "--market-dir", str(self.root), "--key", str(self.key_path), "--ledger", str(self.ledger)]
        environment = {**os.environ, "PYTHONPATH": str(EXPERIMENTS)}
        self.process = subprocess.Popen(command, cwd=EXPERIMENTS, env=environment,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        url = f"http://127.0.0.1:{port}"
        for _ in range(80):
            if self.process.poll() is not None:
                out, err = self.process.communicate(); self.fail(f"seller exited: {out} {err}")
            try:
                response = httpx.get(url + "/.well-known/agent-card.json", timeout=.2)
                if response.status_code == 200: return url
            except httpx.HTTPError: pass
            time.sleep(.025)
        self.fail("local seller did not become ready")

    def stop_server(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill(); self.process.wait(timeout=3)
        if self.process:
            if self.process.stdout: self.process.stdout.close()
            if self.process.stderr: self.process.stderr.close()
        self.process = None

    def test_real_http_a2a_trade_signature_and_restart_deduplication(self):
        url = self.start_server()
        with httpx.Client(timeout=2) as http:
            card = http.get(url + "/.well-known/agent-card.json").json()
            self.assertEqual(card["supportedInterfaces"][0]["protocolVersion"], "1.0")
            receipt = BOriginalClient(url, http).buy("G-LAMP-01", list_price_from_catalog(self.root/"catalog.json", "G-LAMP-01"), 140)
        from scripts.market_kit import pub_b64, verify_seller
        self.assertTrue(verify_seller(pub_b64(self.key), receipt))
        self.assertEqual(receipt["unit"], "test-unit-g1")
        self.stop_server()
        persisted = TradeLedger(self.ledger)
        self.assertTrue(persisted.contains(unit_id="test-unit-g1"))
        url = self.start_server()
        with httpx.Client(timeout=2) as http:
            with self.assertRaises(CandidateBuyerError):
                BOriginalClient(url, http).buy("G-LAMP-01", 150, 140)

    def test_ledger_write_failure_keeps_previous_complete_file(self):
        ledger = TradeLedger(self.ledger)
        ledger.append({"transaction_id": "t1", "order_id": "o1", "unit_id": "u1",
                       "status": "a2a_agreed", "rejection_reason": ""})
        before = self.ledger.read_bytes()
        with patch("operational_candidate.ledger.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(LedgerError):
                ledger.append({"transaction_id": "t2", "order_id": "o2", "unit_id": "u2",
                               "status": "rejected", "rejection_reason": "test"})
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertFalse(TradeLedger(self.ledger).contains(transaction_id="t2"))

    def test_official_shaped_fixture_files_and_placeholders(self):
        catalog = json.loads((self.root/"catalog.json").read_text())
        inventory = json.loads((self.root/"inventory.json").read_text())
        order = json.loads((self.root/"order.json").read_text())
        self.assertIn(order["acceptable"][0], catalog)
        self.assertTrue(any(unit["sku"] == order["acceptable"][0] for unit in inventory["units"]))
        candidate = EXPERIMENTS / "operational_candidate"
        for path in candidate.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".json", ".md"}:
                text = path.read_text(errors="ignore")
                self.assertNotIn("TEST_KEY_PATH_REQUIRED", text)
                self.assertNotIn("TEST_LIST_PRICE_REQUIRED", text)

    def test_restock_dry_run_has_no_buy_or_balance_change(self):
        catalog = json.loads((self.root/"catalog.json").read_text())
        inventory = json.loads((self.root/"inventory.json").read_text())
        demand = json.loads((self.root/"demand.json").read_text())
        result = restock_preview(inventory, catalog, demand)
        self.assertEqual(result["buy"], [])
        self.assertEqual(result["balance_before"], result["balance_after"])
        self.assertEqual(result["would_buy"], [{"sku": "G-LAMP-01", "qty": 2,
            "estimated_unit_cost": 100, "estimated_total": 200, "within_limits": True}])


if __name__ == "__main__": unittest.main()
