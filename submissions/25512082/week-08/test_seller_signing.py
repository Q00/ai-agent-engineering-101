from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from seller import canon, sign_receipt


class SellerSigningTests(unittest.TestCase):
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
