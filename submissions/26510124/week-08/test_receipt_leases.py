"""Payment leases expire only while the guarded checkout can revoke them."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from feedback import atomic_json
from inventory import Inventory, StockError
from receipt_leases import LeaseError, ReceiptLeaseStore
from test_inventory import unit


ROOT = Path(__file__).resolve().parent


def receipt(task="t1", item_unit="u1", price=85000):
    return {"shop": "fixture", "item": "G00001", "price": price, "task": task,
            "sig_seller": "synthetic-fixture", "unit": item_unit,
            "cert": True, "claim": "B", "warranty_hours": 3}


def child_cancel(folder):
    ReceiptLeaseStore(folder).cancel("t1", "cs1")


class ReceiptLeaseTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = ReceiptLeaseStore(self.folder)
        self.store.initialize()

    def issue(self, task="t1", item_unit="u1", ttl=300):
        return self.store.issue(task, item_unit, receipt(task, item_unit), ttl=ttl)

    def test_expiry_revokes_before_releasing_and_never_revives_old_receipt(self):
        with patch("receipt_leases.time.time", return_value=1000):
            self.issue()
            self.store.checkout("t1", "cs1")
        with patch("receipt_leases.time.time", return_value=1300):
            self.assertEqual(self.store.sweep(), {})
            with self.assertRaises(LeaseError):
                self.store.authorize("t1", receipt())
            self.issue("t2")
            with self.assertRaises(LeaseError):
                self.store.begin_settle("t1", "cs1")
            self.assertEqual(ReceiptLeaseStore(self.folder).holds(), {"u1": "t2"})

    def test_cancel_releases_and_expired_unknown_legacy_never_auto_releases(self):
        atomic_json(self.folder / "receipt-reservations.json", {"legacy-unit": "old-task"})
        self.issue()
        self.store.checkout("t1", "cs1")
        with self.assertRaises(LeaseError):
            self.store.cancel("t1", "wrong")
        self.store.cancel("t1", "cs1")
        self.store.cancel("t1", "cs1")  # idempotent
        with patch("receipt_leases.time.time", return_value=9e10):
            self.assertEqual(self.store.sweep(), {"legacy-unit": "old-task"})
        with self.assertRaises(LeaseError):
            self.store.authorize("old-task")

    def test_settling_and_unknown_result_survive_expiry_cancel_and_missing_stock(self):
        with patch("receipt_leases.time.time", return_value=1000):
            self.issue()
            self.store.checkout("t1", "cs1")
            self.store.begin_settle("t1", "cs1")
        with patch("receipt_leases.time.time", return_value=5000):
            self.assertEqual(self.store.sweep(), {"u1": "t1"})
            with self.assertRaises(LeaseError):
                self.store.cancel("t1", "cs1")
            with self.assertRaises(LeaseError):
                self.store.finish("t1", "revoked")
            self.store.finish("t1", "uncertain")
            self.assertEqual(self.store.reconcile([]), {"u1": "t1"})
            self.store.finish("t1", "completed")
            self.assertEqual(self.store.reconcile(["u1"]), {"u1": "t1"})
            self.assertEqual(self.store.reconcile([]), {})
            self.assertEqual(self.store.holds(), {})

    def test_original_unsigned_terms_restored_and_signed_tampering_rejected(self):
        self.issue()
        forged = {**receipt(), "unit": "other", "claim": "A", "warranty_hours": 100}
        row = self.store.authorize("t1", forged)
        self.assertEqual(row["receipt"], receipt())
        for changes in ({"price": 1}, {"price": True}, {"item": "different"}, {"sig_seller": "wrong"}):
            with self.assertRaises(LeaseError):
                self.store.authorize("t1", {**receipt(), **changes})
        row["receipt"]["price"] = 1
        self.assertEqual(self.store.get("t1")["receipt"]["price"], 85000)

    def test_duplicate_session_and_repeat_settlement_rejected(self):
        self.issue()
        self.store.checkout("t1", "cs1")
        self.store.checkout("t1", "cs1")
        with self.assertRaises(LeaseError):
            self.store.checkout("t1", "cs2")
        self.store.begin_settle("t1", "cs1")
        with self.assertRaises(LeaseError):
            self.store.begin_settle("t1", "cs1")

    def test_confirmed_validation_refusal_revokes_current_attempt_and_releases(self):
        self.issue()
        self.store.checkout("t1", "cs1")
        self.store.begin_settle("t1", "cs1")
        with self.assertRaises(LeaseError):
            self.store.refuse("t1", "wrong-session")
        self.assertEqual(self.store.holds(), {"u1": "t1"})
        self.store.refuse("t1", "cs1")
        self.assertEqual(self.store.holds(), {})
        with self.assertRaises(LeaseError):
            self.store.authorize("t1", receipt())
        self.issue("t2")
        self.assertEqual(self.store.holds(), {"u1": "t2"})

    def test_validation_refusal_cannot_release_uncertain_or_completed_payment(self):
        for terminal in ("uncertain", "completed"):
            task, item_unit = terminal + "-task", terminal + "-unit"
            self.issue(task, item_unit)
            self.store.checkout(task, "cs1")
            self.store.begin_settle(task, "cs1")
            self.store.finish(task, terminal)
            with self.assertRaises(LeaseError):
                self.store.refuse(task, "cs1")
            self.assertEqual(self.store.holds()[item_unit], task)

    def test_legacy_recovery_only_after_explicit_evidence_keeps_known_payments(self):
        atomic_json(self.folder / "receipt-reservations.json", {"legacy": "old-task", "not-owned": "other"})
        self.issue()
        self.store.checkout("t1", "cs1")
        self.store.begin_settle("t1", "cs1")
        self.store.finish("t1", "uncertain")
        with self.assertRaises(LeaseError):
            self.store.recover_legacy({"legacy"}, {})
        result = self.store.recover_legacy({"legacy", "u1"}, {"verified_guard": "fixture"})
        self.assertEqual(result["count"], 1)
        document = json.loads(Path(result["backup"]).read_text())
        self.assertEqual(document["holds"], {"legacy": "old-task"})
        self.assertEqual(self.store.holds(), {"not-owned": "other", "u1": "t1"})
        self.assertEqual(self.store.get("old-task")["status"], "revoked")
        with self.assertRaises(LeaseError):
            self.store.authorize("old-task")
        self.assertEqual(self.store.recover_legacy({"legacy"}, {"verified_guard": "fixture"})["count"], 0)
        self.assertEqual(json.loads(Path(result["backup"]).read_text()), document)

    def test_partial_write_repairs_projection_without_reauthorizing_revoked_task(self):
        self.issue()
        atomic_json(self.folder / "receipt-reservations.json", {})
        self.assertEqual(self.store.holds(), {"u1": "t1"})
        self.store.finish("t1", "revoked")
        atomic_json(self.folder / "receipt-reservations.json", {"u1": "t1"})
        self.assertEqual(ReceiptLeaseStore(self.folder).holds(), {})
        self.assertEqual(self.store.get("t1")["status"], "revoked")

    def test_state_corruption_and_persist_failure_retain_reservation(self):
        self.issue()
        original = (self.folder / "receipt-reservations.json").read_bytes()
        with patch("receipt_leases.durable_json", side_effect=OSError("fixture disk failure")):
            with self.assertRaises(LeaseError):
                self.store.finish("t1", "revoked")
        self.assertEqual((self.folder / "receipt-reservations.json").read_bytes(), original)
        self.assertEqual(self.store.get("t1")["status"], "issued")
        (self.folder / "receipt-leases.json").write_text('{"version": 1, "leases": invalid}')
        with self.assertRaises(LeaseError):
            self.store.holds()
        self.assertEqual((self.folder / "receipt-reservations.json").read_bytes(), original)

    def test_guarded_inventory_observes_other_process_cancel_without_restart(self):
        atomic_json(self.folder / "inventory.json", {"round": 3, "units": [unit()]})
        with patch.dict(os.environ, {"MARKET_RECEIPT_GUARD": "1"}):
            inventory = Inventory(self.folder)
            inventory.reserve("t1", "G00001")
            inventory.issue("t1", receipt=receipt())
            self.store.checkout("t1", "cs1")
            with self.assertRaises(StockError):
                inventory.reserve("t2", "G00001")
            process = multiprocessing.get_context("fork").Process(target=child_cancel, args=(str(self.folder),))
            process.start()
            process.join(10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(inventory.reserve("t2", "G00001")["unit"], "u1")

    def test_guarded_inventory_missing_registry_refuses_to_issue(self):
        (self.folder / "receipt-leases.json").unlink()
        atomic_json(self.folder / "inventory.json", {"round": 3, "units": [unit()]})
        with patch.dict(os.environ, {"MARKET_RECEIPT_GUARD": "1"}):
            with self.assertRaises(StockError):
                Inventory(self.folder)

    def test_initialize_preserves_legacy_and_rejects_broken_maps(self):
        atomic_json(self.folder / "receipt-reservations.json", {"u1": "t1"})
        self.store.initialize()
        self.assertEqual(self.store.holds(), {"u1": "t1"})
        atomic_json(self.folder / "receipt-reservations.json", {"u1": {"invalid": True}})
        with self.assertRaises(LeaseError):
            self.store.initialize()

    def test_terminal_history_bounded_but_unknown_payment_never_pruned(self):
        self.issue()
        self.store.checkout("t1", "cs1")
        self.store.begin_settle("t1", "cs1")
        self.store.finish("t1", "uncertain")
        state = json.loads((self.folder / "receipt-leases.json").read_text())
        for i in range(600):
            task, item_unit = f"old-{i}", f"sold-{i}"
            state["leases"][task] = {"receipt": receipt(task, item_unit), "unit": item_unit,
                                      "issued_at": i, "deadline": i + 300,
                                      "status": "revoked", "session_id": None}
        atomic_json(self.folder / "receipt-leases.json", state)
        self.issue("new-task", "new-unit")
        state = json.loads((self.folder / "receipt-leases.json").read_text())
        self.assertEqual(len(state["leases"]), 514)
        self.assertEqual(state["leases"]["t1"]["status"], "uncertain")
        with self.assertRaises(LeaseError):
            self.store.authorize("old-0", receipt("old-0", "sold-0"))

    def test_crash_after_revocation_commit_releases_projection_on_next_load(self):
        self.issue()
        from receipt_leases import durable_json
        def fail_only_hold_write(path, value):
            if Path(path).name == "receipt-reservations.json":
                raise OSError("fixture interrupted projection write")
            return durable_json(path, value)
        with patch("receipt_leases.durable_json", side_effect=fail_only_hold_write):
            with self.assertRaises(LeaseError):
                self.store.finish("t1", "revoked")
        self.assertEqual(ReceiptLeaseStore(self.folder).holds(), {})
        with self.assertRaises(LeaseError):
            self.store.authorize("t1", receipt())


if __name__ == "__main__":
    unittest.main()
