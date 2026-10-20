import base64
import json
import unittest
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from receipt_signing import sign_receipt
from seller_agent import DEFAULT, Seller, build
from test_seller import send, task


def verified_body(receipt):
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    body["price"] = int(body["price"])
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class ReceiptTests(unittest.TestCase):
    def test_a2a_receipt_is_signed_and_bound_to_task_and_price(self):
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key), \
                patch("seller_agent.opening_price", return_value=145), \
                patch("seller_agent.seller_decision", return_value={"action": "accept", "price": 110}):
            with TestClient(build({**DEFAULT, "name": "행복 가게", "sign_receipts": True})) as client:
                first = task(send(client, "I want a lamp"))
                done = task(send(client, "110", first["id"]))
        rec = done["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(rec["task"], first["id"])
        self.assertEqual(rec["shop"], "행복 가게")
        self.assertEqual(rec["price"], 110)
        signature = base64.b64decode(rec["sig_seller"], validate=True)
        key.public_key().verify(signature, verified_body(rec))
        for field, value in [("price", 111), ("task", "another-task"), ("item", "chair"), ("shop", "other")]:
            with self.subTest(field=field), self.assertRaises(InvalidSignature):
                key.public_key().verify(signature, verified_body({**rec, field: value}))

    def test_noninteger_prices_are_not_silently_rounded(self):
        for price in (110.5, True, "110"):
            with self.assertRaises(ValueError):
                sign_receipt(Ed25519PrivateKey.generate(), shop="s", item="lamp", price=price, task="t")

    def test_missing_key_fails_closed_only_in_round2_mode(self):
        with patch("seller_agent.load_shop_key", side_effect=FileNotFoundError("missing key")) as loader:
            Seller(DEFAULT)
            loader.assert_not_called()
            with self.assertRaises(FileNotFoundError):
                Seller({**DEFAULT, "sign_receipts": True})


if __name__ == "__main__":
    unittest.main()
