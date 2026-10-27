"""Crypto boundaries checked with ephemeral in-memory test keys."""
import base64
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from negotiation.receipts import canonical_receipt, sign_receipt, verify_receipt
from negotiation.a2a_client import ProtocolError


class ReceiptSignatureTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
        self.body = {"shop": "서찬 가게", "item": "lamp", "price": 100, "task": "task-example"}

    def signed(self):
        body = dict(self.body)
        body["sig_seller"] = base64.b64encode(self.key.sign(canonical_receipt(body))).decode()
        return body

    def test_utf8_sorted_keys_and_integer_float_have_same_signature_bytes(self):
        expected = json.dumps(self.body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        self.assertEqual(canonical_receipt(self.body), expected)
        self.assertEqual(canonical_receipt({**self.body, "price": 100.0}), expected)
        self.assertTrue(verify_receipt({**self.signed(), "price": 100.0}, self.public))

    def test_changes_to_any_signed_field_or_wrong_key_fail(self):
        for field, value in {"shop": "different", "item": "chair", "price": 101, "task": "replay"}.items():
            self.assertFalse(verify_receipt({**self.signed(), field: value}, self.public))
        other = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.Raw,
                                                                       serialization.PublicFormat.Raw)
        self.assertFalse(verify_receipt(self.signed(), base64.b64encode(other).decode()))
        self.assertFalse(verify_receipt({**self.body, "sig_seller": "invalid"}, self.public))

    def test_fractional_boolean_and_nonfinite_prices_are_rejected(self):
        for price in [True, 100.5, float("nan"), float("inf"), "100"]:
            with self.assertRaises(ProtocolError):
                canonical_receipt({**self.body, "price": price})

    def test_signing_uses_the_provided_key_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test-key"
            path.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            signed = sign_receipt(self.body, path)
        self.assertTrue(verify_receipt(signed, self.public))
        self.assertNotIn("sig_seller", self.body)
        self.assertIs(type(signed["price"]), int)


if __name__ == "__main__":
    unittest.main()
