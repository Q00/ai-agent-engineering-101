"""Signature integrity, fail-closed completion, and clear negotiation outcomes."""

import base64
import json
import re
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

import seller


def decision(action, price):
    return {"action": action, "price": price, "reason": "local policy fixture"}


class SignatureChecks(unittest.TestCase):
    def setUp(self):
        self.key = serialization.load_pem_private_key((seller.ROOT / "shop_key.pem").read_bytes(), password=None)

    def test_canonical_utf8_signature_and_tamper_detection(self):
        body = {"shop": "철원 가게", "item": "lamp", "price": 110, "task": "synthetic-task"}
        receipt = seller.sign_receipt(body)
        self.assertNotIn("sig_seller", body)
        self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]), seller.canon(body))
        for field, value in (("price", 111), ("item", "chair"), ("task", "other"), ("shop", "other")):
            with self.subTest(field=field), self.assertRaises(InvalidSignature):
                self.key.public_key().verify(base64.b64decode(receipt["sig_seller"]),
                                             seller.canon({**body, field: value}))

    def test_signing_rejects_non_integer_and_invalid_prices(self):
        for price in (True, 110.0, 0, -1):
            with self.subTest(price=price), self.assertRaises(ValueError):
                seller.sign_receipt({"shop": "test", "item": "lamp", "price": price, "task": "test"})

    def test_opening_guides_keep_all_reserve_guards(self):
        for item, reserve in seller.RESERVES.items():
            self.assertGreaterEqual(seller.opening_reference(item), reserve)
            with self.assertRaises(ValueError):
                seller.validate_opening(decision("ask", reserve - 1), reserve)
            with self.assertRaises(ValueError):
                seller.validate_followup(decision("accept", reserve - 1), reserve, reserve - 1)


class SellerProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build_app("claude", 12345)),
                                       base_url="http://test", headers={"A2A-Version": "1.0"})
        self.addAsyncCleanup(self.client.aclose)

    async def send(self, text, task_id=None):
        msg = {"role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": text}]}
        if task_id:
            msg["taskId"] = task_id
        response = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
                                          "method": "SendMessage", "params": {"message": msg}})
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"].get("task", payload["result"])

    async def test_event_visible_in_public_card_with_manual_terms(self):
        card = (await self.client.get("/.well-known/agent-card.json")).json()
        self.assertEqual(card["version"], "0.3.0")
        self.assertIn("4- or 5-star", card["description"])
        self.assertIn("Payment is manual", card["description"])
        self.assertIn("no fixed rate/auto payout", card["description"])
        self.assertIn("in bulk after event", card["description"])
        self.assertIn("2026-10-21 09:51", card["description"])
        self.assertIn("10-28 09:51 KST", card["description"])
        self.assertLessEqual(len(card["description"]), 300)

    async def test_promotion_does_not_break_last_number_asking_price(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 120)):
            opened = await self.send("lamp")
        text = opened["status"]["message"]["parts"][0]["text"]
        self.assertIn("Review cashback event", text)
        self.assertEqual(re.findall(r"\d+", text)[-1], "120")
        self.assertTrue(text.endswith("lamp: asking 120"))
        canceled = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
            "method": "CancelTask", "params": {"id": opened["id"]}})
        self.assertNotIn("error", canceled.json())

    async def test_completed_receipt_matches_agreement_and_task(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 120)), \
             patch.object(seller, "followup_decision", return_value=decision("accept", 110)):
            opened = await self.send("lamp")
            done = await self.send("110", opened["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        receipt = done["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(receipt["task"], opened["id"])
        self.assertEqual(receipt["price"], 110)
        self.assertIn("sig_seller", receipt)
        self.assertEqual(set(receipt), {"shop", "item", "price", "task", "sig_seller"})
        self.assertIn("agreed price 110", done["status"]["message"]["parts"][0]["text"])
        self.assertIn("Review cashback event", done["status"]["message"]["parts"][0]["text"])

    async def test_rejection_explains_offer_and_quote_without_disclosing_reserve(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 300)), \
             patch.object(seller, "followup_decision", return_value=decision("reject", None)):
            opened = await self.send("bookshelf")
            done = await self.send("230", opened["id"])
        text = done["status"]["message"]["parts"][0]["text"]
        self.assertEqual(done["status"]["state"], "TASK_STATE_REJECTED")
        self.assertIn("offer 230", text)
        self.assertIn("quoted price was 300", text)
        self.assertNotIn("260", text)
        self.assertFalse(done.get("artifacts"))

    async def test_signing_failure_never_publishes_unsigned_sale(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 120)), \
             patch.object(seller, "followup_decision", return_value=decision("accept", 110)), \
             patch.object(seller, "sign_receipt", side_effect=OSError("fixture key unavailable")), \
             self.assertLogs(seller.LOGGER, level="ERROR"):
            opened = await self.send("lamp")
            done = await self.send("110", opened["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_FAILED")
        self.assertFalse(done.get("artifacts"))


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
