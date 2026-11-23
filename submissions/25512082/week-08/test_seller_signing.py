from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from seller import canon, sign_receipt, terms


class SellerSigningTests(unittest.TestCase):
    def test_grade_targets_keep_the_five_percent_cost_floor(self):
        base = {"list_price": 87_900, "cost": 26_302}
        self.assertEqual(terms({**base, "grade": "A"}), (68_562, 27_618))
        self.assertEqual(terms({**base, "grade": "B"}), (63_288, 27_618))
        self.assertEqual(terms({**base, "grade": "C"}), (57_135, 27_618))

    def test_unknown_grade_retains_original_target(self):
        self.assertEqual(terms({"list_price": 10_000, "cost": 5_000}), (8_200, 5_250))

    def test_floor_recovers_accrued_storage_before_margin(self):
        unit = {"list_price": 10_000, "cost": 5_000, "grade": "C", "supplied_at": 1_000}
        # Two days of 1% storage add 100 credits before the 5% floor.
        self.assertEqual(terms(unit, now=1_000 + 2 * 86_400, storage_per_day=0.01), (6_500, 5_355))

    def test_receipt_has_required_fields_and_verifiable_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "shop_key.pem"
            key = Ed25519PrivateKey.generate()
            key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            receipt = sign_receipt(
                {"shop": "Atlas Shop", "item": "lamp", "price": 120, "task": "task-1"},
                key_path,
            )
            body = {name: receipt[name] for name in ("shop", "item", "price", "task")}
            key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon(body))
            self.assertIs(type(receipt["price"]), int)

    def test_signature_rejects_changed_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "shop_key.pem"
            key = Ed25519PrivateKey.generate()
            key_path.write_bytes(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            receipt = sign_receipt(
                {"shop": "Atlas Shop", "item": "lamp", "price": 120, "task": "task-1"},
                key_path,
            )
            changed = {"shop": receipt["shop"], "item": receipt["item"], "price": 121, "task": receipt["task"]}
            with self.assertRaises(InvalidSignature):
                key.public_key().verify(base64.b64decode(receipt["sig_seller"]), canon(changed))


if __name__ == "__main__":
    unittest.main()
