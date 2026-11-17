import logging
import unittest
from unittest.mock import AsyncMock

from offer_validation import OfferParsingError, negotiate_followup


class OfferValidationTests(unittest.IsolatedAsyncioTestCase):
    async def run_decisions(self, text, decisions):
        self.model = AsyncMock(side_effect=decisions)
        return await negotiate_followup({}, text, 3000, 3800, self.model, logging.getLogger("test"))

    async def test_repeated_offer_holds_without_model_but_improvement_can_negotiate(self):
        model = AsyncMock(return_value={"action": "offer", "price": 3500, "parsed_offer": 3300})
        request = {"taskId": "t1", "itemId": "sku1", "seenBuyerOffers": [3200]}
        with self.assertLogs("test", level="INFO") as logs:
            choice, offer = await negotiate_followup(request, "3,200", 3000, 3700, model, logging.getLogger("test"))
        self.assertEqual((choice["price"], offer), (3700, 3200))
        self.assertIn("task=t1 sku=sku1", logs.output[0])
        model.assert_not_awaited()
        choice, _ = await negotiate_followup(request, "3300", 3000, 3700, model, logging.getLogger("test"))
        self.assertEqual(choice["price"], 3500)
        model.assert_awaited_once()

    async def test_repeated_natural_language_offer_cannot_trigger_discount(self):
        model = AsyncMock(return_value={"action": "accept", "price": 3200, "parsed_offer": 3200})
        choice, _ = await negotiate_followup({"seenBuyerOffers": [3200]}, "I offer 3200", 3000, 3700,
                                             model, logging.getLogger("test"))
        self.assertEqual((choice["action"], choice["price"]), ("offer", 3700))

    async def test_reserve_skips_model_and_rechecks_rising_floor(self):
        model = AsyncMock(side_effect=AssertionError("model called at floor"))
        for text, action, price in [("2900", "offer", 3001), ("3001", "accept", 3001),
                                    ("Can you go lower?", "clarify", None)]:
            choice, _ = await negotiate_followup({}, text, 3001, 3000, model, logging.getLogger("test"))
            self.assertEqual((choice["action"], choice.get("price")), (action, price))
        model.assert_not_awaited()

    async def test_repeated_offer_meeting_current_ask_still_accepts(self):
        model = AsyncMock()
        choice, _ = await negotiate_followup({"seenBuyerOffers": [3800]}, "3800", 3000, 3800,
                                             model, logging.getLogger("test"))
        self.assertEqual(choice["action"], "accept")
        model.assert_not_awaited()

    async def test_no_price_clarifies_without_acceptance_or_retry(self):
        for value in (None, 0):
            with self.subTest(value=value):
                choice, offer = await self.run_decisions("Can you go lower?", [
                    {"action": "accept", "price": 3800, "parsed_offer": value}])
                self.assertEqual(choice["action"], "clarify")
                self.assertIsNone(offer)
                self.assertEqual(self.model.await_count, 1)

    async def test_plain_offer_meeting_ask_accepts_without_model(self):
        for text in ("3,800", "4000"):
            choice, offer = await self.run_decisions(text, [])
            self.assertEqual((choice["action"], choice["price"]), ("accept", offer))
            self.model.assert_not_awaited()

    async def test_grounded_natural_language_offer_can_be_accepted(self):
        choice, offer = await self.run_decisions("I offer 3,800.", [
            {"action": "accept", "price": 3800, "parsed_offer": 3800}])
        self.assertEqual(offer, choice["price"])

    async def test_invented_offer_cannot_be_accepted(self):
        for text in ("Agreed", "I offer -3800", "I offer 3800.50", "buy G3800"):
            with self.subTest(text=text):
                invalid = {"action": "accept", "price": 3800, "parsed_offer": 3800}
                with self.assertRaises(OfferParsingError):
                    await self.run_decisions(text, [invalid, invalid])

    async def test_invalid_values_are_not_no_offer(self):
        for value in (False, -1, 0.0, "3800", 3800.0):
            with self.subTest(value=value):
                invalid = {"action": "accept", "price": 3800, "parsed_offer": value}
                with self.assertRaises(OfferParsingError):
                    await self.run_decisions("I offer 3800", [invalid, invalid])

    async def test_acceptance_must_match_offer_and_reserve(self):
        for text, offer, price in (("3500", 3500, 3600), ("2900", 2900, 2900)):
            invalid = {"action": "accept", "price": price, "parsed_offer": offer}
            with self.assertRaises(OfferParsingError):
                await self.run_decisions(text, [invalid, invalid])

    async def test_grounded_offer_meeting_ask_does_not_counter(self):
        choice, offer = await self.run_decisions("I offer 3800", [
            {"action": "offer", "price": 4000, "parsed_offer": 3800}])
        self.assertEqual(choice, {"action": "accept", "price": 3800, "parsed_offer": 3800})
        self.assertEqual(self.model.await_count, 1)

    async def test_missing_field_and_malformed_json_are_parsing_failures(self):
        for invalid in ({"action": "offer", "price": 3700}, ValueError("Invalid JSON")):
            with self.assertRaises(OfferParsingError):
                await self.run_decisions("Please lower the price", [invalid, invalid])
