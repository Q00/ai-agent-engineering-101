"""Price source checks and semantic metadata boundaries; no live model claims."""
import json
import unittest

import httpx

from negotiation.a2a_client import ProtocolError, metadata_ask
from negotiation.config import Settings
from negotiation.contracts import DecisionError
from negotiation.harness import decide
from negotiation.price_reading import PriceContext, parse_price_reading


def reading(action="price", price=95, evidence="I'll go to 95", **changes):
    return json.dumps({"action": action, "price": price, "evidence": evidence,
                       "reason": "The current committed offer.", **changes})


class PriceReadingTests(unittest.TestCase):
    def setUp(self):
        self.text = "Other shop 90, friend paid 85, so I'll go to 95, delivery within 3 days."
        self.context = PriceContext("buyer", "lamp", 130, self.text)

    def test_model_selects_a_nonfinal_number_and_code_preserves_it(self):
        self.assertEqual(parse_price_reading(reading(), self.context).price, 95)

    def test_fabricated_evidence_or_price_is_rejected(self):
        for raw in (reading(evidence="I'll go to 120", price=120), reading(price=120)):
            with self.assertRaises(DecisionError):
                parse_price_reading(raw, self.context)

    def test_invalid_numeric_types_and_duplicate_keys_are_rejected(self):
        for amount in (True, "95", 95.5, -95, 0, None):
            with self.assertRaises(DecisionError):
                parse_price_reading(reading(price=amount), self.context)
        with self.assertRaisesRegex(DecisionError, "duplicate_json_key"):
            parse_price_reading('{"action":"price","action":"none"}', self.context)

    def test_previous_requires_the_exact_verified_ask(self):
        context = PriceContext("buyer", "lamp", 130, "I accept your current ask; delivery within 3 days.")
        self.assertEqual(parse_price_reading(reading("previous", 130, "I accept your current ask"), context).price, 130)
        with self.assertRaisesRegex(DecisionError, "reference_must_equal_previous_price"):
            parse_price_reading(reading("previous", 3, "I accept your current ask"), context)

    def test_no_offer_or_ambiguity_never_contains_a_binding_price(self):
        for action in ("none", "ambiguous"):
            self.assertIsNone(parse_price_reading(reading(action, None, ""), self.context).price)
            with self.assertRaises(DecisionError):
                parse_price_reading(reading(action), self.context)

    def test_decimal_and_comma_literals_can_anchor_an_integer(self):
        for text, amount in (("I offer 95.00, delivery 3 days.", 95), ("I offer 1,200; delivery 3 days.", 1200)):
            context = PriceContext("buyer", "lamp", 130, text)
            self.assertEqual(parse_price_reading(reading(price=amount, evidence=text), context).price, amount)

    def test_negative_number_is_not_evidence_for_its_positive_magnitude(self):
        context = PriceContext("buyer", "lamp", 130, "I offer -95.")
        with self.assertRaisesRegex(DecisionError, "selected_price_not_in_evidence"):
            parse_price_reading(reading(evidence="I offer -95"), context)

    def test_raw_text_is_sent_only_as_untrusted_observation(self):
        class Log:
            def record(self, *args, **kwargs):
                pass
        def handler(request):
            body = json.loads(request.content)
            data = json.loads(body["input"])
            self.assertEqual(set(data["trusted_context"]), {"speaker", "item_id", "previous_price"})
            self.assertEqual(data["observation"]["counterparty_text_untrusted"], self.text)
            return httpx.Response(200, json={"status": "completed", "output": [{"type": "message",
                "content": [{"type": "output_text", "text": reading()}]}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            value = decide("price", self.context, {}, Settings("fake-offline-key"), Log(), client)
        self.assertEqual(value.price, 95)


def task(task_ask=130, message_ask=130):
    value = {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {
        "parts": [{"text": "Old price 190; current ask 130, delivery 3 days."}]}}}
    if task_ask is not None:
        value["metadata"] = {"ask": task_ask}
    if message_ask is not None:
        value["status"]["message"]["metadata"] = {"ask": message_ask}
    return value


class AskMetadataTests(unittest.TestCase):
    def test_sdk_integer_float_and_either_location_are_supported(self):
        for value in (task(130.0, 130.0), task(None, 130), task(130, None)):
            self.assertEqual(metadata_ask(value), 130)

    def test_missing_metadata_never_falls_back_to_text(self):
        with self.assertRaisesRegex(ProtocolError, "ask_metadata_missing"):
            metadata_ask(task(None, None))

    def test_conflicting_metadata_is_not_silently_preferred(self):
        with self.assertRaisesRegex(ProtocolError, "ask_metadata_conflict"):
            metadata_ask(task(130, 110))

    def test_invalid_price_types_and_terminal_stale_ask_are_rejected(self):
        for amount in (True, "130", 130.5, float("nan"), float("inf"), 0, -1):
            with self.assertRaises(ProtocolError):
                metadata_ask(task(amount, None))
        value = task()
        value["status"]["state"] = "TASK_STATE_COMPLETED"
        with self.assertRaises(ProtocolError):
            metadata_ask(value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
