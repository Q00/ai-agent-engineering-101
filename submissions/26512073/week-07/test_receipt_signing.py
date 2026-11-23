"""Offline checks using the existing shop identity; never prints the private key."""
import base64
from pathlib import Path
import unittest

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from seller import canon, sign_receipt


class ReceiptTests(unittest.TestCase):
    def test_valid_and_changed_receipt(self):
        key_path = Path(__file__).resolve().parent / "shop_key.pem"
        original_key = key_path.read_bytes()
        public = serialization.load_pem_private_key(original_key, password=None).public_key()
        body = {"shop": "Afford Shop", "item": "lamp", "price": 100, "task": "offline-test"}
        signed = sign_receipt(dict(body))
        signature = base64.b64decode(signed["sig_seller"])
        public.verify(signature, canon(body))
        with self.assertRaises(InvalidSignature):
            public.verify(signature, canon(dict(body, price=120)))
        with self.assertRaises(InvalidSignature):
            public.verify(signature, canon(dict(body, task="another-task")))
        self.assertEqual(original_key, key_path.read_bytes())
        self.assertEqual(set(signed), {"shop", "item", "price", "task", "sig_seller"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
