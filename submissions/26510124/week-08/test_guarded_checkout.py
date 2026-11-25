"""Real signed receipts and guarded ACP against an entirely synthetic market."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from guarded_checkout import GuardedAcpShop
import market_runtime
from receipt_leases import ReceiptLeaseStore
from receipt_signing import sign_receipt
import run_round3
from sales_performance import SETTLED as PERFORMANCE_SETTLED
from shop_diagnostics import SETTLED as DIAGNOSTICS_SETTLED
from test_inventory import unit
from vendor import market_kit_v4 as kit

ROOT = Path(__file__).resolve().parent
PUBLIC = "https://fixture.invalid"
PAYMENT = {"payment_data": {"order": "order-fixture", "checkout_mandate": {}, "payment_mandate": {}}}


class FakeMarket:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.on_inventory = None
        self.on_settle = None
        self.units = [unit()]

    def call(self, method, path, *args, **kwargs):
        self.calls.append((method, path))
        if path == "/api/inventory":
            if self.on_inventory:
                self.on_inventory()
            return httpx.Response(200, json={"round": 3, "units": self.units})
        if path == "/api/settle":
            self.assert_settling = self.store.get("task-1")["status"]
            if self.on_settle:
                return self.on_settle()
            self.units = []
            return httpx.Response(200, json={"ledger": 8, "mandate_receipt": {
                "unit": "u1", "grade": "B", "ledger": 8, "name": "테스트 의자"}})
        raise AssertionError("unexpected synthetic request: " + path)


class GuardedCheckoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        (ROOT / ".market").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.key = Ed25519PrivateKey.generate()
        self.store = ReceiptLeaseStore(self.folder)
        self.store.initialize()
        self.market = FakeMarket(self.store)
        self.shop = GuardedAcpShop(self.key, self.market, self.folder)
        self.receipt = self.receipt_for("task-1")

    def receipt_for(self, task):
        return {**sign_receipt(self.key, shop="dyishappy", item="G00001", price=85000, task=task),
                "unit": "u1", "cert": True, "claim": "B", "warranty_hours": 3}

    def issue(self, receipt=None, ttl=300):
        receipt = receipt or self.receipt
        self.store.issue(receipt["task"], receipt["unit"], receipt, ttl=ttl)

    async def create(self, receipt=None):
        rec = receipt or self.receipt
        return await self.shop.create({"receipt": rec, "line_items": [{"id": rec["item"], "quantity": 1}]}, PUBLIC)

    async def request(self, session, action, payload=None, method="POST"):
        return await self.shop.handle(SimpleNamespace(method=method),
            f"acp/checkout_sessions/{session['id']}/{action}", json.dumps(payload or {}).encode(), PUBLIC)

    async def opened(self):
        self.issue()
        response = await self.create()
        self.assertEqual(response.status_code, 201, response.body)
        return json.loads(response.body)

    async def test_old_signed_receipt_cannot_rebind_unreserved_stock(self):
        response = await self.create()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.market.calls)
        self.assertFalse(self.shop.sessions)

    async def test_recorded_receipt_restores_unsigned_terms_after_restart(self):
        self.issue()
        self.shop = GuardedAcpShop(self.key, self.market, self.folder)
        response = await self.create({**self.receipt, "unit": "forged", "claim": "A", "warranty_hours": 999})
        self.assertEqual(response.status_code, 201)
        data = json.loads(response.body)
        self.assertEqual(data["fulfillment"], {"unit": "u1", "cert": unit()["cert"], "claim": "B", "warranty_hours": 3})
        self.assertEqual(self.store.get("task-1")["session_id"], data["id"])

    async def test_cancel_revokes_and_releases_but_old_receipt_stays_denied(self):
        session = await self.opened()
        response = await self.request(session, "cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.holds(), {})
        self.assertEqual((await self.create()).status_code, 409)
        self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 409)
        replacement = self.receipt_for("task-2")
        self.issue(replacement)
        self.assertEqual((await self.create(replacement)).status_code, 201)
        self.assertNotIn(("POST", "/api/settle"), self.market.calls)

    async def test_expired_checkout_releases_upstream_picker_hold_and_denies_completion(self):
        session = await self.opened()
        deadline = self.store.get("task-1")["deadline"]
        with patch("receipt_leases.time.time", return_value=deadline + 1):
            self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 409)
            replacement = self.receipt_for("task-2")
            self.issue(replacement)
            self.assertEqual((await self.create(replacement)).status_code, 201)
        self.assertEqual(self.shop.sessions[session["id"]]["status"], "expired")
        self.assertNotIn(("POST", "/api/settle"), self.market.calls)

    async def test_expiry_while_inventory_request_runs_never_publishes_session(self):
        now = time.time()
        clock = [now]
        with patch("receipt_leases.time.time", side_effect=lambda: clock[0]):
            self.issue(ttl=1)
            self.market.on_inventory = lambda: clock.__setitem__(0, now + 2)
            response = await self.create()
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.shop.sessions)
        self.assertEqual(self.store.get("task-1")["status"], "revoked")

    async def test_concurrent_create_only_publishes_one_session(self):
        self.issue()
        replies = await asyncio.gather(self.create(), self.create())
        self.assertEqual(sorted(r.status_code for r in replies), [201, 409])
        self.assertEqual(len(self.shop.sessions), 1)
        self.assertEqual(self.market.calls.count(("GET", "/api/inventory")), 1)

    async def test_restart_does_not_create_second_session_for_same_receipt(self):
        await self.opened()
        self.shop = GuardedAcpShop(self.key, self.market, self.folder)
        self.assertEqual((await self.create()).status_code, 409)
        self.assertEqual(self.store.get("task-1")["status"], "checkout")

    async def test_confirmed_payment_is_durable_idempotent_and_log_compatible(self):
        session = await self.opened()
        with patch.object(kit, "log") as log:
            response = await self.request(session, "complete", PAYMENT)
        self.assertEqual(response.status_code, 200, response.body)
        self.assertEqual(self.market.assert_settling, "settling")
        self.assertEqual(self.store.get("task-1")["status"], "completed")
        self.assertEqual(self.store.holds(), {"u1": "task-1"})
        message = "12:34:56 " + next(c.args[0] for c in log.call_args_list if "[acp] settled " in c.args[0])
        self.assertIsNotNone(PERFORMANCE_SETTLED.fullmatch(message))
        self.assertIsNotNone(DIAGNOSTICS_SETTLED.fullmatch(message))
        self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 200)
        self.assertEqual((await self.request(session, "cancel")).status_code, 409)
        self.assertEqual(self.market.calls.count(("POST", "/api/settle")), 1)
        rows = [json.loads(line) for line in (self.folder / "checkout-events.jsonl").read_text().splitlines()]
        self.assertEqual([r["stage"] for r in rows],
                         ["checkout_created", "settlement_started", "settlement_confirmed"])
        self.assertEqual(rows[-1]["ledger"], 8)
        self.assertTrue(all(set(r) == {"time", "task", "unit", "stage", "ledger"} for r in rows))

    async def test_timeout_or_ambiguous_refusal_retains_hold_past_deadline(self):
        for failure in (httpx.ReadTimeout("fixture timeout"), httpx.Response(503, json={"error": "fixture"})):
            with self.subTest(failure=type(failure).__name__):
                # Use an independent lifecycle for each uncertain settlement.
                folder = self.folder / type(failure).__name__
                self.store = ReceiptLeaseStore(folder)
                self.store.initialize()
                self.market = FakeMarket(self.store)
                self.shop = GuardedAcpShop(self.key, self.market, folder)
                def result():
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                self.market.on_settle = result
                session = await self.opened()
                self.assertGreaterEqual((await self.request(session, "complete", PAYMENT)).status_code, 500)
                self.assertEqual(self.store.get("task-1")["status"], "uncertain")
                with patch("receipt_leases.time.time", return_value=time.time() + 10000):
                    self.assertEqual(self.store.sweep(), {"u1": "task-1"})
                    self.assertEqual((await self.request(session, "cancel")).status_code, 409)
                    self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 409)
                self.assertEqual(self.market.calls.count(("POST", "/api/settle")), 1)

    async def test_explicit_market_validation_refusal_revokes_and_releases_stock(self):
        session = await self.opened()
        self.market.on_settle = lambda: httpx.Response(400, json={
            "checks": {"order_live": False, "buyer_signature": True}})
        response = await self.request(session, "complete", PAYMENT)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(self.market.assert_settling, "settling")
        self.assertEqual(self.store.get("task-1")["status"], "revoked")
        self.assertEqual(self.store.holds(), {})
        self.assertEqual((await self.create()).status_code, 409)
        self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 409)
        replacement = self.receipt_for("task-2")
        self.issue(replacement)
        self.assertEqual((await self.create(replacement)).status_code, 201)
        self.assertEqual(self.market.calls.count(("POST", "/api/settle")), 1)

    async def test_unstructured_or_ambiguous_market_errors_never_release(self):
        cases = [(400, {"error": "expired"}), (403, {"checks": {}}),
                 (422, {"checks": {"order_live": "false"}}),
                 (409, {"checks": {"order_live": True}}),
                 (400, {"checks": {"order_live": False}, "ledger": None}),
                 (400, {"checks": {"order_live": False}, "mandate_receipt": None}),
                 (500, {"checks": {"order_live": False}}),
                 (503, {"checks": {"order_live": False}})]
        for index, (status, body) in enumerate(cases):
            with self.subTest(status=status, body=body):
                folder = self.folder / str(index)
                self.store = ReceiptLeaseStore(folder)
                self.store.initialize()
                self.market = FakeMarket(self.store)
                self.shop = GuardedAcpShop(self.key, self.market, folder)
                self.market.on_settle = lambda: httpx.Response(status, json=body)
                session = await self.opened()
                self.assertEqual((await self.request(session, "complete", PAYMENT)).status_code, 502)
                self.assertEqual(self.store.get("task-1")["status"], "uncertain")
                self.assertEqual(self.store.holds(), {"u1": "task-1"})

    async def test_disconnect_during_payment_retains_uncertain_hold(self):
        session = await self.opened()
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(5)
            return httpx.Response(200, json={"ledger": 8, "mandate_receipt": {"unit": "u1", "grade": "B"}})
        self.market.on_settle = slow
        job = asyncio.create_task(self.request(session, "complete", PAYMENT))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
            self.assertEqual(self.store.get("task-1")["status"], "uncertain")
            self.assertEqual(self.store.holds(), {"u1": "task-1"})
        finally:
            release.set()

    async def test_payment_cannot_start_when_durable_state_write_fails(self):
        session = await self.opened()
        with patch.object(self.shop.leases, "begin_settle", side_effect=OSError("fixture disk full")):
            response = await self.request(session, "complete", PAYMENT)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(("POST", "/api/settle"), self.market.calls)

    async def test_cancel_cannot_release_stock_while_settlement_is_in_flight(self):
        session = await self.opened()
        entered, release = threading.Event(), threading.Event()
        def slow():
            entered.set()
            release.wait(5)
            return httpx.Response(200, json={"ledger": 8, "mandate_receipt": {"unit": "u1", "grade": "B"}})
        self.market.on_settle = slow
        payment = asyncio.create_task(self.request(session, "complete", PAYMENT))
        cancellation = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            cancellation = asyncio.create_task(self.request(session, "cancel"))
            await asyncio.sleep(0)
            self.assertFalse(cancellation.done())
            self.assertEqual(self.store.get("task-1")["status"], "settling")
            self.assertEqual(self.store.holds(), {"u1": "task-1"})
            release.set()
            self.assertEqual((await payment).status_code, 200)
            self.assertEqual((await cancellation).status_code, 409)
            self.assertEqual(self.store.get("task-1")["status"], "completed")
        finally:
            release.set()
            await asyncio.gather(payment, *([cancellation] if cancellation else []), return_exceptions=True)

    async def test_invalid_payload_and_get_cannot_cancel_a_receipt(self):
        session = await self.opened()
        self.assertEqual((await self.request(session, "cancel", method="GET")).status_code, 405)
        self.assertEqual((await self.request(session, "complete", {"payment_data": []})).status_code, 400)
        self.assertEqual(self.store.get("task-1")["status"], "checkout")
        self.assertNotIn(("POST", "/api/settle"), self.market.calls)

    async def test_public_guard_card_reports_runtime_without_credentials(self):
        first = self.shop.card(PUBLIC)
        self.assertEqual(first["protocol"], "acp")
        self.assertEqual(first["x_receipt_guard"], self.shop.card(PUBLIC)["x_receipt_guard"])
        self.assertEqual(set(first["x_receipt_guard"]), {"version", "runtime_id", "started_at"})
        self.assertEqual(first["x_receipt_guard"]["version"], 1)


class GuardedLauncherTests(unittest.TestCase):
    def test_wrapper_installs_guard_before_opt_in_and_preserves_pinned_kit(self):
        original = run_round3.verify_kit().read_bytes()
        with patch.object(market_runtime, "ReceiptLeaseStore") as store, \
                patch.object(kit, "AcpShop", kit.AcpShop), \
                patch.dict(os.environ, {}, clear=False), patch.object(kit, "main") as main:
            def started():
                self.assertIs(kit.AcpShop, GuardedAcpShop)
                self.assertEqual(os.environ.get("MARKET_RECEIPT_GUARD"), "1")
                store.return_value.initialize.assert_called_once_with()
            main.side_effect = started
            market_runtime.main()
            main.assert_called_once_with()
        self.assertEqual(run_round3.verify_kit().read_bytes(), original)

    def test_join_launches_guard_wrapper_instead_of_bare_vendor(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".market") as temp:
            folder = Path(temp)
            (folder / "shop-adaptive.json").write_text('{"name":"fixture"}')
            with patch.object(run_round3, "ROOT", folder), \
                    patch.object(run_round3, "verify_kit", return_value=folder / "vendor" / "market_kit_v4.py"), \
                    patch.object(run_round3, "identity", return_value=[]), \
                    patch.object(run_round3.shutil, "which", return_value="/fixture/bin/command"), \
                    patch.object(run_round3.sys, "argv", ["run_round3.py", "--join"]), \
                    patch.dict(os.environ, {"MARKET_OWNER": "fixture-owner"}), \
                    patch.object(run_round3.os, "execv", side_effect=RuntimeError("captured exec")) as execute, \
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "captured exec"):
                    run_round3.main()
                self.assertEqual(execute.call_args.args[1][1], str(folder / "market_runtime.py"))
                self.assertIn("--tunnel", execute.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
