"""The sales pitch must not break numeric buyers or signed receipts."""
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from buyer_agent import last_asking_price, message_text
from seller_agent import DEFAULT, build
from test_receipt_signing import verified_body
from test_seller import send, task


class MarketingTests(unittest.TestCase):
    def test_owner_pitch_and_review_request_preserve_prices_and_signature(self):
        config = json.loads(Path(__file__).with_name("shop-round2.json").read_text())
        key = Ed25519PrivateKey.generate()
        with patch("seller_agent.load_shop_key", return_value=key), \
                patch("seller_agent.opening_price", return_value=150) as opening, \
                patch("seller_agent.seller_decision", side_effect=[
                    {"action": "counter", "price": 135},
                    {"action": "counter", "price": 120},
                    {"action": "accept", "price": 120},
                ]) as decision, \
                TestClient(build({**DEFAULT, **config, "sign_receipts": True})) as client:
            card = client.get("/.well-known/agent-card.json").json()
            self.assertIn("owner", card["description"])
            self.assertIn("signed receipt", card["skills"][0]["description"])
            first = task(send(client, "I want to buy a lamp"))
            tid = first["id"]
            responses = [first, task(send(client, "90", tid)), task(send(client, "110", tid))]
            for response, expected in zip(responses, [150, 135, 120]):
                text = message_text(response)
                self.assertIn("owner", text)
                self.assertNotIn("coupon", text.lower())
                self.assertNotIn("please leave", text.lower())
                self.assertEqual(last_asking_price(text), expected)
            completed = task(send(client, "120", tid))
        self.assertEqual(completed["status"]["state"], "TASK_STATE_COMPLETED")
        closing = message_text(completed)
        self.assertIn("please leave a positive review", closing)
        self.assertIn("If this was a good transaction", closing)
        self.assertNotIn("coupon", closing.lower())
        self.assertEqual(last_asking_price(closing), 120)
        receipt = completed["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(receipt["price"], 120)
        self.assertEqual(receipt["task"], tid)
        key.public_key().verify(base64.b64decode(receipt["sig_seller"]), verified_body(receipt))
        self.assertEqual(opening.call_args.kwargs["strategy"], config["sales_strategy"]["goal"])
        self.assertEqual(decision.call_args.kwargs["strategy"], config["sales_strategy"]["goal"])


if __name__ == "__main__":
    unittest.main()
