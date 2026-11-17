import logging
import unittest
from unittest.mock import AsyncMock

from offer_validation import OfferParsingError, negotiate_followup


class OfferValidationTests(unittest.IsolatedAsyncioTestCase):
    async def run_decisions(self, text, decisions):
        self.model = AsyncMock(side_effect=decisions)
        return await negotiate_followup({}, text, 3000, 3800, self.model, logging.getLogger("test"))

    async def test_no_price_clarifies_without_acceptance_or_retry(self):
        for value in (None, 0):
            with self.subTest(value=value):
                choice, offer = await self.run_decisions("Can you go lower?", [
                    {"action": "accept", "price": 3800, "parsed_offer": value}])
                self.assertEqual(choice["action"], "clarify")
                self.assertIsNone(offer)
                self.assertEqual(self.model.await_count, 1)

    async def test_plain_positive_price_cannot_be_classified_as_missing(self):
        choice, offer = await self.run_decisions("3,800", [
            {"action": "offer", "price": 3700, "parsed_offer": 0},
            {"action": "accept", "price": 3800, "parsed_offer": 3800}])
        self.assertEqual((choice["action"], offer), ("accept", 3800))
        retry = self.model.await_args_list[1].args[0]
        self.assertEqual(retry["numericOffer"], 3800)
        self.assertIn("3800", retry["validationFeedback"])

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
                    await self.run_decisions("3800", [invalid, invalid])

    async def test_acceptance_must_match_offer_and_reserve(self):
        for text, offer, price in (("3500", 3500, 3600), ("2900", 2900, 2900)):
            invalid = {"action": "accept", "price": price, "parsed_offer": offer}
            with self.assertRaises(OfferParsingError):
                await self.run_decisions(text, [invalid, invalid])

    async def test_meeting_ask_still_requires_acceptance(self):
        invalid = {"action": "offer", "price": 4000, "parsed_offer": 3800}
        with self.assertRaises(OfferParsingError):
            await self.run_decisions("3800", [invalid, invalid])

    async def test_missing_field_and_malformed_json_are_parsing_failures(self):
        for invalid in ({"action": "offer", "price": 3700}, ValueError("Invalid JSON")):
            with self.assertRaises(OfferParsingError):
                await self.run_decisions("Please lower the price", [invalid, invalid])
