"""TEST ONLY referral claims, arithmetic and disclosures; no real market orders."""
from pathlib import Path
from copy import deepcopy
import json
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from referral_pricing import code_claim, referral_terms, review_notice
from a2a.helpers import new_text_part
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Message, Role, SendMessageRequest, Task, TaskState, TaskStatus
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from evidence import EvidenceError
import seller
import market_buyer
from test_rank_pricing import board
from test_seller_cancel import ITEM, RecordingQueue, RecordingTrace, StubPolicy


RULE = {"enabled": True, "code": "TESTCODE10", "percent": 10,
        "phases": ["recovery"], "item_ids": ["lamp", "chair", "bookshelf"]}


class ReferralPricingTests(unittest.TestCase):
    def test_exact_discount_on_actual_standard_price_never_goes_below_floor(self):
        for name, base, floor, final in (("lamp", 120, 100, 108), ("chair", 200, 180, 180),
                                          ("bookshelf", 290, 260, 261)):
            terms = referral_terms({"id": name, "reserve": floor}, base,
                {"phase": "recovery", "review_referral": RULE}, "TEST_ONLY_CODE_CLAIM")
            self.assertTrue(terms["applied"])
            self.assertEqual(terms["price"], final)
            self.assertEqual(terms["saving"] * 100, base * 10)
            self.assertFalse(terms["read_verified"])

    def test_no_code_or_wrong_phase_or_item_never_gets_a_discount(self):
        item = {"id": "lamp", "reserve": 100}
        for phase, name, source in (("recovery", "lamp", None), ("acquisition", "lamp", "test"),
                                    ("normal", "lamp", "test"), ("recovery", "lamp-other", "test")):
            terms = referral_terms({**item, "id": name}, 120,
                {"phase": phase, "review_referral": RULE}, source)
            self.assertFalse(terms["applied"])
            self.assertEqual(terms["price"], 120)

    def test_floor_and_fractional_saving_are_unavailable_not_silently_clamped(self):
        for base in (100, 101, 110):
            terms = referral_terms({"id": "lamp", "reserve": 100}, base,
                {"phase": "recovery", "review_referral": RULE}, "test")
            self.assertFalse(terms["applied"])
            self.assertEqual(terms["price"], base)

    def test_claim_requires_code_and_does_not_verify_a_review_read(self):
        self.assertEqual(code_claim("lamp testcode10", {}, RULE), "message.text")
        self.assertEqual(code_claim("lamp", {"referralCode": "TESTCODE10"}, RULE),
                         "message.metadata.referralCode")
        for text in ("I read your review", "lamp TESTCODE100", "lamp XTESTCODE10", "lamp TESTCODE10_extra"):
            self.assertIsNone(code_claim(text, {}, RULE))
        self.assertIsNone(code_claim("TESTCODE10", {}, {}))
        notice = review_notice(RULE)
        self.assertIn("Ad (my shop)", notice)
        self.assertIn("eligible standard quotes", notice)
        self.assertIn("Promotional prices excluded", notice)


class ReferralSellerTests(unittest.IsolatedAsyncioTestCase):
    async def test_code_quotes_are_frozen_no_double_discount_and_receipt_is_exact(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(seller, "Trace", RecordingTrace), \
                patch.object(seller, "load_catalog", return_value=[deepcopy(ITEM)]), \
                patch.object(seller, "StrategyPolicy", StubPolicy), \
                patch.object(seller, "load_shop_key", return_value=Ed25519PrivateKey.generate()):
            shop = seller.Seller({"name": "TEST ONLY referral shop",
                "pricing_state": str(Path(directory) / "state.json")})
            observed = [board(2)]; clock = [0]; contexts = []; decisions = []
            shop.rank_pricing.fetch_board = lambda: observed[0]
            shop.rank_pricing.clock = lambda: clock[0]

            def model(trusted, **kwargs):
                contexts.append(deepcopy(trusted))
                if trusted["opening"]:
                    action, price = "open", trusted["candidates"][0]["max_opening_ask"]
                    if trusted["candidates"][0].get("referral_terms", {}).get("applied"):
                        # A second discount would be above the floor at 100,
                        # but must still fail because it isn't the approved quote.
                        with self.assertRaises(EvidenceError):
                            kwargs["validator"]({"action": "open", "price": 100, "offer": None,
                                "item_id": "lamp", "reason": "TEST ONLY invalid extra discount"})
                else:
                    bid = trusted["offer"]
                    exact = trusted["exact_quote_required"]
                    action = "accept" if bid == trusted["last_ask"] or not exact and bid >= trusted["last_ask"] else "counter"
                    price = bid if action == "accept" else trusted["last_ask"]
                answer = {"action": action, "price": price, "offer": trusted["offer"],
                          "item_id": "lamp", "reason": "TEST ONLY explicit model fixture"}
                decisions.append(answer)
                return kwargs["validator"](answer)
            shop.harness.decide = model
            queue = RecordingQueue()

            def context(text, name):
                task = Task(id=name, context_id=name, status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED))
                return RequestContext(call_context=ServerCallContext(), task=task, task_id=name, context_id=name,
                    request=SendMessageRequest(message=Message(message_id="TEST_ONLY_MESSAGE", role=Role.ROLE_USER,
                                                              parts=[new_text_part(text)])))

            await shop.execute(context("lamp", "standard"), queue)
            self.assertEqual(shop.open["standard"]["ask"], 120)
            await shop.execute(context("lamp REVIEW10", "referral"), queue)
            self.assertEqual(shop.open["referral"]["ask"], 108)
            self.assertEqual(contexts[-1]["candidates"][0]["referral_terms"]["reference_price"], 120)
            await shop.execute(context("120", "referral"), queue)
            self.assertEqual(decisions[-1]["action"], "counter", "Never overcharge a referral quote")
            self.assertEqual(shop.open["referral"]["ask"], 108)
            observed[0] = board(4); clock[0] = 61
            await shop.execute(context("lamp REVIEW10", "acquisition"), queue)
            self.assertEqual(shop.open["acquisition"]["ask"], 100)
            self.assertFalse(shop.open["acquisition"]["referral_terms"]["applied"])
            await shop.execute(context("108", "referral"), queue)
            await shop.execute(context("108", "referral"), queue)
            sales = [e for e in shop.trace.events if e["event"] == "sale_completed"]
            redemptions = [e for e in shop.trace.events if e["event"] == "review_referral_redeemed"]
            self.assertEqual(len(sales), 1)
            self.assertEqual(len(redemptions), 1)
            self.assertEqual(sales[0]["receipt"]["price"], 108)
            self.assertEqual(sales[0]["receipt"]["reviewReferral"]["saving"], 12)
            self.assertFalse(redemptions[0]["read_verified"])
            self.assertEqual(contexts[-1]["pricing_phase"], "recovery")
            # Newly quoting acquisition must not rewrite a previously issued
            # standard quote either; it remains 120 on the original task.
            await shop.execute(context("108", "standard"), queue)
            self.assertEqual(shop.open["standard"]["ask"], 120)


class HonestReferralReviewTests(unittest.TestCase):
    def test_completed_review_score_does_not_depend_on_competitor_position(self):
        responses = []
        for name, score in (("TEST_ONLY_HIGHER", 10), ("TEST_ONLY_LOWER", 0)):
            candidate = {"name": name, "url": "https://TEST-ONLY.invalid", "score": score,
                         "reviews": [], "personal_memory": {}, "card_flags": [], "card": {}}
            memory = Mock()
            memory.ingest_kit_feedback.return_value = []
            memory.read.return_value = {}
            model = Mock()
            model.decide.return_value = {"action": "select_shop", "item_id": name, "reason": "TEST ONLY"}
            result = {"status": "completed", "receipt": {"price": 100, "sig_seller": "TEST_ONLY_NO_REAL_SIGNATURE"}}
            with patch.object(market_buyer, "rank_shops", return_value=[candidate]), \
                    patch.object(market_buyer, "check_card", side_effect=lambda shop: shop), \
                    patch.object(market_buyer, "Harness", return_value=model), \
                    patch.object(market_buyer, "negotiate", return_value=deepcopy(result)):
                response = market_buyer.buy([candidate], "lamp", 120, memory, RecordingTrace())
            self.assertEqual(response["receipt"], result["receipt"])
            self.assertTrue(response["review"].startswith("Bought lamp for 100 credits, within my budget.\n\n"))
            self.assertIn("Ad (my shop)", response["review"])
            self.assertIn("REVIEW10", response["review"])
            self.assertNotIn("black", response["review"].lower())
            responses.append(response)
        self.assertEqual(responses[0]["stars"], responses[1]["stars"])
        self.assertEqual(responses[0]["review"], responses[1]["review"])
