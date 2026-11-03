import asyncio
import json
from pathlib import Path
import runpy
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import seller_r3
from kit_runtime import install
from market_state import read, write, task_file


class Reservations(unittest.TestCase):
    def test_old_released_offer_cannot_clear_a_new_hold_on_same_unit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            for task, status in (("old", "refused"), ("new", "ready")):
                write(task_file(tmp, "signed-offers", task), {"task": task, "unit": "u", "expires_at": 1000})
                write(task_file(tmp, "checkout-events", task), {"status": status})
            s = seller_r3.Seller(); s.prune(100)
            self.assertIn("u", s.committed_units)

    def test_abandoned_negotiation_expires_but_inflight_does_not(self):
        s = seller_r3.Seller()
        s.open = {"old": {"touched": 0}, "busy": {"touched": 0}}
        s.busy.add("busy")
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            s.prune(181)
        self.assertEqual(set(s.open), {"busy"})

    def test_restart_restores_hold_and_only_definitive_failure_releases(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            md = Path(tmp)
            write(task_file(md, "signed-offers", "t"), {"task": "t", "unit": "u", "expires_at": 100})
            for status, held in (("ready", True), ("processing", True), ("uncertain", True),
                                 ("completed", True), ("refused", False), ("canceled", False)):
                write(task_file(md, "checkout-events", "t"), {"status": status})
                s = seller_r3.Seller()
                s.prune(50)
                self.assertEqual("u" in s.committed_units, held, status)

    def test_timeout_release_requires_guard_and_waits_for_uncertain_payment(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3, "MARKET_DIR", Path(tmp)):
            md = Path(tmp)
            write(task_file(md, "signed-offers", "t"), {"task": "t", "unit": "u", "expires_at": 100})
            with patch.dict("os.environ", {"MARKET_LEASE_GUARD": "0"}):
                s = seller_r3.Seller(); s.prune(200)
                self.assertIn("u", s.committed_units)
            with patch.dict("os.environ", {"MARKET_LEASE_GUARD": "1"}):
                s.prune(200)
                self.assertNotIn("u", s.committed_units)
                write(task_file(md, "checkout-events", "t"), {"status": "uncertain"})
                s.prune(200)
                self.assertIn("u", s.committed_units)


class ACPRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_expired_replay_never_releases_an_uncertain_payment(self):
        root = Path(__file__).resolve().parents[3]
        ns = runpy.run_path(str(root / "scripts/market_kit.py"), run_name="kit_test")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ"):
            install(ns)
            write(task_file(tmp, "signed-offers", "t"), {"expires_at": 0})
            write(task_file(tmp, "checkout-events", "t"), {"status": "uncertain"})
            acp = ns["main"].__globals__["AcpShop"](Ed25519PrivateKey.generate(), None, Path(tmp))
            self.assertEqual((await acp.create({"receipt": {"task": "t"}}, "url")).status_code, 409)
            self.assertEqual(read(task_file(tmp, "checkout-events", "t"))["status"], "uncertain")

    async def test_real_kit_adapter_cancels_failed_payment_and_rejects_replay(self):
        root = Path(__file__).resolve().parents[3]
        ns = runpy.run_path(str(root / "scripts/market_kit.py"), run_name="kit_test")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        response = SimpleNamespace(status_code=402, headers={"content-type": "application/json"},
                                   json=lambda: {"error": "budget"})
        market = SimpleNamespace(call=lambda *a, **kw: response)
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ"):
            install(ns)
            acp = ns["main"].__globals__["AcpShop"](Ed25519PrivateKey.generate(), market, Path(tmp))
            acp.sessions["s"] = {"task": "t", "status": "ready_for_payment", "checkout_jwt": "jwt"}
            result = await acp.handle(SimpleNamespace(method="POST"), "acp/checkout_sessions/s/complete", b"{}", "url")
            self.assertEqual(result.status_code, 402)
            self.assertEqual(acp.sessions["s"]["status"], "canceled")
            self.assertEqual(read(task_file(tmp, "checkout-events", "t"))["status"], "refused")
            retry = await acp.create({"receipt": {"task": "t"}}, "url")
            self.assertEqual(retry.status_code, 409)

    async def test_expired_receipt_rejected_before_signature_or_market_call(self):
        root = Path(__file__).resolve().parents[3]
        ns = runpy.run_path(str(root / "scripts/market_kit.py"), run_name="kit_test")
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ"):
            install(ns)
            write(task_file(tmp, "signed-offers", "t"), {"expires_at": 0})
            acp = ns["main"].__globals__["AcpShop"](Ed25519PrivateKey.generate(), None, Path(tmp))
            result = await acp.create({"receipt": {"task": "t"}}, "url")
            self.assertEqual(result.status_code, 409)


if __name__ == "__main__":
    unittest.main()

