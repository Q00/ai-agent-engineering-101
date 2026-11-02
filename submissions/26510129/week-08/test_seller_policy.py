import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import pricing
from seller_guard import ReserveSigner, canonical


class PricingExperimentTest(unittest.TestCase):
    def _task_for(self, item, wanted):
        for n in range(1000):
            task = f"task-{n}"
            if pricing.policy_arm(item, task) == wanted:
                return task
        self.fail(f"no task assigned to {wanted}")

    def test_task_assignment_is_stable(self):
        first = pricing.policy_arm({"id": "lamp"}, "same-task")
        self.assertEqual(first, pricing.policy_arm({"id": "lamp"}, "same-task"))

    def test_arms_make_distinct_safe_asks_with_healthy_close_rate(self):
        item = {"id": "lamp", "name": "lamp", "reserve": 100}
        rows = []
        for n in range(20):
            rows.extend([
                {"kind": "open", "task": f"old-{n}", "item": "lamp", "ask": 110},
                {"kind": "deal", "task": f"old-{n}", "item": "lamp", "price": 103},
            ])
        with patch.object(pricing, "load", return_value=rows), patch.object(pricing, "reviews", return_value=[]):
            volume, _ = pricing.suggest(item, self._task_for(item, "volume"))
            margin, _ = pricing.suggest(item, self._task_for(item, "margin"))
        self.assertGreaterEqual(volume, 105)
        self.assertGreater(margin, volume)

    def test_every_arm_holds_the_reserve_floor(self):
        item = {"id": "chair", "name": "chair", "reserve": 180}
        rows = [{"kind": "deal", "task": f"d-{n}", "item": "chair", "price": 150} for n in range(20)]
        with patch.object(pricing, "load", return_value=rows), patch.object(pricing, "reviews", return_value=[]):
            for arm in ("volume", "margin"):
                ask, _ = pricing.suggest(item, self._task_for(item, arm))
                self.assertGreaterEqual(ask, 189)


class ReserveSignerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key_path = Path(self.tmp.name) / "shop_key.pem"
        self.key = Ed25519PrivateKey.generate()
        self.key_path.write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        self.signer = ReserveSigner("shop", "lamp", 100, "task-1", self.key_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_protect_raises_counter_to_reserve(self):
        self.assertEqual(self.signer.protect(90, "counter"), 100)

    def test_signer_binds_fields_and_signature(self):
        receipt = self.signer.sign(105)
        body = {key: receipt[key] for key in ("shop", "item", "price", "task")}
        self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canonical(body))
        self.assertEqual(body, {"shop": "shop", "item": "lamp", "price": 105, "task": "task-1"})

    def test_signer_refuses_below_reserve(self):
        with self.assertRaises(ValueError):
            self.signer.sign(99)


if __name__ == "__main__":
    unittest.main()
