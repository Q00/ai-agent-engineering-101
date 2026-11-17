"""Contract tests for isolation, upstream forge persistence and payment boundaries.

All generated tools and wallets here are test fixtures, not assignment solutions.
"""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_support import ROOT, load_kit, offline_context, verify_vendor
from offline_demo import tool_fixture

kit = load_kit()


class ForgeTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".local"
        scratch.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="test-", dir=scratch)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "logs").mkdir()
        self.ctx = offline_context()
        self.tb = kit.Toolbox(self.root, self.ctx)

    def test_verified_source(self):
        self.assertEqual(verify_vendor(), 6)

    def test_failed_replacement_keeps_working_tool_and_logs_failure(self):
        self.assertEqual(self.tb.forge(tool_fixture())["test"], "passed")
        self.assertEqual(self.tb.forge(tool_fixture(broken=True))["test"], "failed")
        self.assertEqual(self.tb.call("count_fixture", {"values": [1]})["result"], {"count": 1})
        rows = [json.loads(s) for s in (self.root / "logs/forge.jsonl").read_text().splitlines()]
        self.assertEqual([r["passed"] for r in rows], [True, False])
        self.assertEqual(self.tb.tools["count_fixture"]["version"], 1)

    def test_version_reload_and_tamper_rejection(self):
        self.tb.forge(tool_fixture())
        self.tb.forge(tool_fixture(version=2))
        self.assertTrue((self.root / "tools/.deprecated/count_fixture@1/forge.json").is_file())
        reloaded = kit.Toolbox(self.root, self.ctx)
        self.assertEqual(reloaded.call("count_fixture", {})["result"], {"count": 0})
        path = self.root / "tools/count_fixture/logic.py"
        path.write_text(path.read_text() + "\n# intentionally changed in this test fixture\n")
        self.assertNotIn("count_fixture", kit.Toolbox(self.root, self.ctx).tools)

    def test_sandbox_blocks_network_process_and_undeclared_write(self):
        operations = ["import socket; socket.create_connection(('example.com', 443), timeout=1)",
                      "import subprocess; subprocess.run(['echo', 'must-not-run'])",
                      "from pathlib import Path; Path('../outside.txt').write_text('blocked')"]
        for operation in operations:
            with self.subTest(operation=operation):
                args = tool_fixture()
                args["logic"] = "def run(args, kit):\n    " + operation + "\n"
                args["test"] = "def test(run, kit):\n    run({}, kit)\n"
                result = self.tb.forge(args)
                self.assertEqual(result["test"], "failed")
                self.assertIn("PermissionError", result["error"])
                self.assertNotIn("count_fixture", self.tb.tools)
        self.assertFalse((self.root.parent / "outside.txt").exists())

    def test_offline_context_cannot_grant_wallet_or_identity(self):
        for permission in ({"wallet": ["pay"]}, {"identity": ["buyer"]}):
            with self.subTest(permission=permission), self.assertRaises(PermissionError):
                self.ctx["signer"].grant("fixture", permission, 5)
        self.assertEqual(self.ctx["tokens"], {})
        self.assertEqual(self.ctx["addresses"], {})

    def test_parent_path_is_not_a_valid_write_permission(self):
        args = tool_fixture()
        args["permission"]["write"] = ["../week-08/agent.json"]
        self.assertIn("write paths stay inside", self.tb.forge(args))
        self.assertEqual(list(self.tb.tools), [])


class PaymentBoundaryTests(unittest.TestCase):
    def setUp(self):
        # No signer HTTP server; fresh ephemeral wallet stays only in memory.
        self.signer = kit.Signer.__new__(kit.Signer)
        self.wallet = kit.maroo.Wallet.generate()
        self.signer.keys = {"buyer": self.wallet}
        self.signer.caps = {}
        self.cap = self.signer.grant("fixture", {"wallet": ["pay", "sign"],
                                                "identity": ["buyer"], "pay_limit": 1000}, 30)
        self.request = {"cap": self.cap, "op": "pay", "role": "buyer", "to": self.wallet.address,
                        "wei": kit.maroo.CREDIT_WEI * 500, "data": "", "testing": True}

    def test_test_mode_never_sends_money(self):
        with patch.object(kit.maroo, "pay", side_effect=AssertionError("RPC must not run")) as pay:
            self.assertEqual(self.signer.do(self.request), "0x" + "0" * 64)
            pay.assert_not_called()

    def test_excess_negative_wrong_role_and_expired_cap_are_denied(self):
        cases = [{"wei": 1001 * kit.maroo.CREDIT_WEI}, {"wei": -1}, {"role": "shop"},
                 {"cap": "unknown-fixture-capability"}]
        with patch.object(kit.maroo, "pay", side_effect=AssertionError("RPC must not run")):
            for changes in cases:
                with self.subTest(changes=changes), self.assertRaises(PermissionError):
                    self.signer.do(dict(self.request, **changes))
            self.signer.caps[self.cap]["until"] = time.time() - 1
            with self.assertRaises(PermissionError):
                self.signer.do(self.request)

    def test_eip191_signature_verifies_only_original_payload(self):
        data = {"purpose": "offline-signature-test", "amount": 7}
        sig = self.signer.do({"cap": self.cap, "op": "sign", "role": "buyer", "obj": data})
        self.assertTrue(kit.maroo.verify(self.wallet.address, sig, kit.mk.canon(data)))
        self.assertFalse(kit.maroo.verify(self.wallet.address, sig, kit.mk.canon(dict(data, amount=8))))

    def test_sent_transfer_is_recoverable_when_capability_is_revoked(self):
        transaction = "0x" + "1" * 64  # Explicit fake RPC result, not a real transaction.
        with patch.object(kit.maroo, "pay", return_value=transaction) as pay:
            self.assertEqual(self.signer.do(dict(self.request, testing=False)), transaction)
            pay.assert_called_once()
        records = self.signer.revoke(self.cap)
        self.assertEqual(records[0]["tx_hash"], transaction)
        with self.assertRaises(PermissionError):
            self.signer.do(self.request)

    def test_round4_documented_fee_example(self):
        fees = kit.maroo.fees(100_000, bps=100, network=420)
        self.assertEqual(fees, {"shop": 1210, "buyer": 1210, "market": 2000})
        self.assertEqual(100_000 - fees["shop"], 98_790)
        self.assertEqual(100_000 + fees["buyer"], 101_210)


if __name__ == "__main__":
    unittest.main()
