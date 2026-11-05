"""Synthetic migration and deployment fences; no SSH or market calls."""
import io
import asyncio
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from deploy import install_server as server
from feedback import atomic_json
from receipt_leases import LeaseError, ReceiptLeaseStore
from recover_receipts import recover, SnapshotNotReady
from guarded_checkout import GuardedAcpShop
from seller_r3 import InventorySeller
from seller_agent import DEFAULT, build
from test_adaptive_seller import send
from test_inventory import unit
from test_guarded_checkout import FakeMarket

ROOT = Path(__file__).resolve().parent


class ReceiptRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / ".market"
        self.md.mkdir()
        self.now = time.time()
        self.card = {"x_receipt_guard": {"version": 1, "runtime_id": "a" * 32,
                                         "started_at": self.now - 5}}
        atomic_json(self.md / "receipt-reservations.json", {"u1": "legacy", "u2": "not_owned"})
        atomic_json(self.md / "inventory.json", {"round": 3, "units": [{"unit": "u1"}]})
        self.store = ReceiptLeaseStore(self.md)
        self.store.initialize()

    def test_preview_has_no_writes_and_only_owned_unknown_holds_recover(self):
        before = {p.name: p.read_bytes() for p in self.md.iterdir() if p.is_file()}
        preview = recover(self.root, self.card, self.now - 10, now=self.now)
        self.assertEqual(preview["legacy_candidates"], 1)
        self.assertFalse(preview["applied"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.md.iterdir() if p.is_file()})
        result = recover(self.root, self.card, self.now - 10, apply=True, now=self.now)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.store.holds(), {"u2": "not_owned"})
        self.assertTrue(Path(result["backup"]).exists())
        with self.assertRaises(LeaseError):
            self.store.authorize("legacy")
        self.assertEqual(recover(self.root, self.card, self.now - 10, apply=True, now=self.now)["count"], 0)

    def test_old_guard_and_old_snapshot_cannot_unlock(self):
        with self.assertRaises(ValueError):
            recover(self.root, {}, self.now - 10, apply=True, now=self.now)
        with self.assertRaises(ValueError):
            recover(self.root, self.card, self.now - 1, apply=True, now=self.now)
        os.utime(self.md / "inventory.json", (self.now - 8, self.now - 8))
        with self.assertRaises(SnapshotNotReady):
            recover(self.root, self.card, self.now - 10, apply=True, now=self.now)
        self.assertEqual(self.store.holds(), {"u1": "legacy", "u2": "not_owned"})

    def test_known_uncertain_lease_is_never_legacy_recovery_target(self):
        self.store.issue("new", "u3", {"task": "new", "unit": "u3", "shop": "s", "item": "G1",
                                        "price": 10, "sig_seller": "fixture"})
        self.store.checkout("new", "cs_new")
        self.store.begin_settle("new", "cs_new")
        self.store.finish("new", "uncertain")
        atomic_json(self.md / "inventory.json", {"round": 3, "units": [{"unit": "u1"}, {"unit": "u3"}]})
        result = recover(self.root, self.card, self.now - 10, apply=True, now=self.now)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.store.get("new")["status"], "uncertain")
        self.assertEqual(self.store.holds()["u3"], "new")

    def test_failed_deploy_cannot_restore_and_start_unguarded_code(self):
        backup = self.root / "backup"
        backup.mkdir()
        source = self.root / "market_runtime.py"
        source.write_text("GUARDED CURRENT SOURCE")
        state = {"files": [{"name": "market_runtime.py", "existed": False}],
                 "was_active": True, "dropin_existed": False}
        with patch.object(server, "systemctl") as calls, redirect_stdout(io.StringIO()):
            server.rollback(self.root, backup, self.root / "dropin", state)
        self.assertTrue(state["rollback_blocked_by_receipt_guard"])
        self.assertEqual(source.read_text(), "GUARDED CURRENT SOURCE")
        self.assertNotIn(unittest.mock.call("start", server.UNIT), calls.call_args_list)

    def test_full_service_stop_required(self):
        def ctl(*args, **kw):
            output = {"--property=KillMode": "control-group", "--property=ControlGroup": "",
                      "--property=MainPID": "0"}
            return SimpleNamespace(stdout="inactive" if args[0] == "is-active" else
                                   next((v for k, v in output.items() if k in args), ""))
        with patch.object(server, "systemctl", side_effect=ctl):
            self.assertGreater(server.stop_for_guarded_deployment(), 0)
        with patch.object(server, "systemctl", return_value=SimpleNamespace(stdout="process")):
            with self.assertRaisesRegex(ValueError, "control-group"):
                server.stop_for_guarded_deployment()

    def test_recovered_inventory_can_issue_checkout_cancel_and_sell_again(self):
        atomic_json(self.md / "inventory.json", {"round": 3, "units": [unit()]})
        result = recover(self.root, self.card, self.now - 10, apply=True, now=self.now)
        self.assertEqual(result["count"], 1)
        key = Ed25519PrivateKey.generate()
        with patch.dict(os.environ, {"MARKET_RECEIPT_GUARD": "1"}), \
                patch("seller_agent.load_shop_key", return_value=key):
            seller = InventorySeller({**DEFAULT, "name": "dyishappy", "market_dir": self.md})
        seller.events = self.root / "events.jsonl"
        market = FakeMarket(self.store)
        checkout = GuardedAcpShop(key, market, self.md)
        with patch("seller_r3.inventory_decision", side_effect=[
                {"action": "counter", "price": 90000}, {"action": "accept", "price": 85000}]), \
                TestClient(build(DEFAULT, seller)) as client:
            opened = send(client, "I want G00001")
            done = send(client, "85000", opened["id"])
        receipt = done["artifacts"][0]["parts"][0]["data"]
        receipt["price"] = int(receipt["price"])
        self.assertEqual(self.store.get(receipt["task"])["status"], "issued")
        response = asyncio.run(checkout.create({"receipt": receipt,
            "line_items": [{"id": receipt["item"], "quantity": 1}]}, "https://fixture.invalid"))
        self.assertEqual(response.status_code, 201, response.body)
        session = json.loads(response.body)
        cancelled = asyncio.run(checkout.handle(SimpleNamespace(method="POST"),
            f"acp/checkout_sessions/{session['id']}/cancel", b"{}", "https://fixture.invalid"))
        self.assertEqual(cancelled.status_code, 200)
        # The already running seller must see checkout's external hold update.
        self.assertEqual(seller.inventory.reserve("another-buyer", "G00001")["unit"], "u1")


if __name__ == "__main__":
    unittest.main()
