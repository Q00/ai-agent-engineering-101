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

    def test_affordable_offers_close_without_counter_or_reject(self):
        for reserve in seller.RESERVES.values():
            for offer in (reserve, reserve + 5):
                seller.validate_followup(decision("accept", offer), reserve, offer)
                for action, price in (("ask", offer + 5), ("reject", None)):
                    with self.subTest(reserve=reserve, offer=offer, action=action), self.assertRaises(ValueError):
                        seller.validate_followup(decision(action, price), reserve, offer)

    def test_opening_uses_validated_config_without_model_inference(self):
        with patch.object(seller, "run_harness") as model, patch.object(seller, "write_decision_log") as log:
            for item in seller.RESERVES:
                opened = seller.opening_decision("claude", item, "fixture", "synthetic-opening")
                self.assertEqual(opened["price"], seller.opening_reference(item))
            model.assert_not_called()
            self.assertEqual(log.call_count, 3)
            self.assertEqual(log.call_args.args[0]["controller"], "configured_price")

    def test_overbid_validation_requires_lower_quote_confirmation(self):
        seller.validate_followup(decision("ask", 105), 100, 115, 105)
        for action, price in (("accept", 115), ("accept", 105), ("ask", 110), ("reject", None)):
            with self.subTest(action=action, price=price), self.assertRaises(ValueError):
                seller.validate_followup(decision(action, price), 100, 115, 105)


class SellerProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Protocol fixtures run inside the event window; time boundaries are
        # tested separately so these tests do not depend on the calendar date.
        event = json.loads((seller.ROOT / "review_event_promise_legacy.json").read_text(encoding="utf-8"))
        active = patch.object(seller, "active_event", return_value=event)
        active.start()
        self.addCleanup(active.stop)
        refund = patch.object(seller, 'refund_policy', return_value=None)
        refund.start()
        self.addCleanup(refund.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build_app("claude", 12345)),
                                       base_url="http://test", headers={"A2A-Version": "1.0"})
        self.addAsyncCleanup(self.client.aclose)

    async def send(self, text, task_id=None, extra_parts=()):
        msg = {"role": "ROLE_USER", "messageId": str(uuid4()), "parts": [{"text": text}, *extra_parts]}
        if task_id:
            msg["taskId"] = task_id
        response = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
                                          "method": "SendMessage", "params": {"message": msg}})
        payload = response.json()
        self.assertNotIn("error", payload)
        return payload["result"].get("task", payload["result"])

    async def test_event_visible_in_public_card_with_manual_terms(self):
        card = (await self.client.get("/.well-known/agent-card.json")).json()
        self.assertEqual(card["version"], "0.9.0")
        self.assertTrue(card["description"].startswith("Fast first quotes. Public-price protection. Signed receipts."))
        self.assertIn("promise a 5-star review", card["description"])
        self.assertIn("lamp 100/chair 180/bookshelf 260", card["description"])
        self.assertIn("No refund", card["description"])
        self.assertIn("Earlier cashback participants keep prior terms", card["description"])
        self.assertIn("2026-10-28 09:51 KST", card["description"])
        self.assertLessEqual(len(card["description"]), 300)

    async def test_promotion_does_not_break_first_or_last_number_asking_price(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 105)):
            opened = await self.send("lamp")
        text = opened["status"]["message"]["parts"][0]["text"]
        self.assertEqual(text.splitlines()[0], "lamp: asking 105")
        self.assertIn("Promise a five-star review to unlock our lowest price.", text)
        self.assertEqual(re.findall(r"\d+", text), ["105"])
        quoted = opened["status"]["message"]["parts"][1]["data"]
        self.assertEqual(quoted["kind"], "quote")
        self.assertEqual(quoted["item"], "lamp")
        self.assertEqual(quoted["price"], 105)
        self.assertIn("promise a 5-star review", quoted["event_terms"])
        canceled = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
            "method": "CancelTask", "params": {"id": opened["id"]}})
        self.assertNotIn("error", canceled.json())

    async def test_overbid_has_no_receipt_until_buyer_confirms_lower_price(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 105)):
            opened = await self.send("lamp")
        with patch.object(seller, "followup_decision") as model, patch.object(seller, "write_decision_log"):
            for _ in range(2):
                pending = await self.send("115", opened["id"])
                self.assertEqual(pending["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
                self.assertFalse(pending.get("artifacts"))
                message = pending["status"]["message"]
                self.assertEqual(re.findall(r"\d+", message["parts"][0]["text"]), ["105"])
                self.assertTrue(message["parts"][1]["data"]["confirmation_required"])
            model.assert_not_called()
        with patch.object(seller, "followup_decision", return_value=decision("accept", 105)):
            done = await self.send("105", opened["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        receipt = done["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(receipt["price"], 105)
        self.assertEqual(receipt["task"], opened["id"])

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
        self.assertIn("No refund", done["status"]["message"]["parts"][0]["text"])

    async def test_explicit_promise_unlocks_floor_before_confirmed_purchase(self):
        with patch.object(seller, "record_promise") as pledge, patch.object(seller, "write_decision_log"), \
             patch.object(seller, "opening_decision") as opening:
            task = await self.send("I want to buy a chair\nI promise a 5-star review.")
            opening.assert_not_called()
            pledge.assert_called_once()
        self.assertEqual(task["status"]["state"], "TASK_STATE_INPUT_REQUIRED")
        self.assertFalse(task.get("artifacts"))
        self.assertEqual(task["status"]["message"]["parts"][0]["text"].splitlines()[0], "chair: asking 180")
        with patch.object(seller, "followup_decision", return_value=decision("accept", 180)):
            done = await self.send("180", task["id"])
        self.assertEqual(done["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(done["artifacts"][0]["parts"][0]["data"]["price"], 180)

    async def test_followup_promise_has_no_automatic_review_or_receipt(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 105)):
            task = await self.send("lamp")
        with patch.object(seller, "record_promise") as pledge, patch.object(seller, "write_decision_log"), \
             patch.object(seller, "followup_decision") as model:
            offered = await self.send("5점 후기를 남기겠습니다.", task["id"])
            model.assert_not_called()
            pledge.assert_called_once()
        data = offered["status"]["message"]["parts"][1]["data"]
        self.assertEqual(data["price"], 100)
        self.assertTrue(data["event_applied"])
        self.assertFalse(data["refund"])
        self.assertFalse(offered.get("artifacts"))
        canceled = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
            "method": "CancelTask", "params": {"id": task["id"]}})
        self.assertNotIn("error", canceled.json())

    async def test_marketing_phrase_can_be_used_as_an_explicit_promise(self):
        with patch.object(seller, "opening_decision", return_value=decision("ask", 195)):
            task = await self.send("chair")
        with patch.object(seller, "record_promise"), patch.object(seller, "write_decision_log"):
            offered = await self.send("I promise a five-star review.", task["id"])
        self.assertEqual(offered["status"]["message"]["parts"][0]["text"], "chair: asking 180")
        self.assertTrue(offered["status"]["message"]["parts"][1]["data"]["event_applied"])
        self.assertFalse(offered.get("artifacts"))
        canceled = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
            "method": "CancelTask", "params": {"id": task["id"]}})
        self.assertNotIn("error", canceled.json())

    async def test_structured_promise_survives_jsonrpc_sdk_conversion(self):
        with patch.object(seller, "record_promise"), patch.object(seller, "write_decision_log"):
            task = await self.send("bookshelf", extra_parts=[
                {"data": {"kind": "review_promise", "stars": 5, "agreed": True}}])
        self.assertEqual(task["status"]["message"]["parts"][1]["data"]["price"], 260)
        self.assertFalse(task.get("artifacts"))
        canceled = await self.client.post("/", json={"jsonrpc": "2.0", "id": str(uuid4()),
            "method": "CancelTask", "params": {"id": task["id"]}})
        self.assertNotIn("error", canceled.json())

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

    async def test_service_questions_get_truthful_terms_before_any_purchase(self):
        opened = await self.send("I want a lamp. Do you provide a quality warranty or refund policy?")
        for task in (opened, await self.send("What after-sales service can you guarantee?", opened['id'])):
            self.assertEqual(task['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
            self.assertFalse(task.get('artifacts'))
            text = task['status']['message']['parts'][0]['text']
            self.assertIn('signed receipt', text)
            self.assertIn('not configured', text)
            self.assertEqual(re.findall(r'\d+', text), ['105'])
        with patch.object(seller, 'followup_decision') as model:
            done = await self.send('I accept the quoted price.', opened['id'])
            model.assert_not_called()
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        self.assertEqual(done['artifacts'][0]['parts'][0]['data']['price'], 105)

    async def test_budget_conditional_or_negative_messages_never_authorize_purchase(self):
        opened = await self.send('chair')
        for message in ('My budget is 195', 'I do not accept 195', 'I offer 195 if you provide a warranty',
                        'I accept the quoted price if you guarantee refunds', '195 or 180', 'I offer 195 for a lamp'):
            task = await self.send(message, opened['id'])
            self.assertEqual(task['status']['state'], 'TASK_STATE_INPUT_REQUIRED', message)
            self.assertFalse(task.get('artifacts'), message)
        done = await self.send('I offer 195 for the chair.', opened['id'])
        self.assertEqual(done['status']['state'], 'TASK_STATE_COMPLETED')
        self.assertEqual(done['artifacts'][0]['parts'][0]['data']['price'], 195)

    async def test_natural_overbid_keeps_lower_quote_until_explicit_confirmation(self):
        opened = await self.send('lamp')
        task = await self.send('I offer 115 for a lamp.', opened['id'])
        self.assertEqual(task['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
        self.assertFalse(task.get('artifacts'))
        self.assertEqual(re.findall(r'\d+', task['status']['message']['parts'][0]['text']), ['105'])
        done = await self.send('105에 구매하겠습니다.', opened['id'])
        self.assertEqual(done['artifacts'][0]['parts'][0]['data']['price'], 105)

    async def test_natural_below_quote_offer_still_uses_existing_negotiation(self):
        opened = await self.send('lamp')
        with patch.object(seller, 'followup_decision', return_value=decision('accept', 100)) as negotiate:
            done = await self.send('I can pay 100.', opened['id'])
        self.assertEqual(negotiate.call_args.args[3], 100)
        self.assertEqual(done['artifacts'][0]['parts'][0]['data']['price'], 100)


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(stream=sys.stdout, verbosity=2))
