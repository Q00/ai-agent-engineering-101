"""Offline acp_pay boundary: a refused completion exposes check names, never response data."""
import sys
from pathlib import Path
import unittest
from unittest import mock
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import market_kit as kit

class FailureChecksTest(unittest.TestCase):
    def pay(self, answer, raw=False):
        messages, why, requests = [], [], []
        def serve(request):
            requests.append(request.url.path)
            if request.url.path.endswith("acp.json"):
                return httpx.Response(200, json={"endpoint": "https://fixture.invalid/acp"})
            if request.url.path.endswith("checkout_sessions"):
                return httpx.Response(201, json={"id": "fixture", "checkout_jwt": "fixture"})
            return httpx.Response(402, text=answer) if raw else httpx.Response(402, json=answer)
        client = httpx.Client(transport=httpx.MockTransport(serve))
        order = {"order": "fixture", "mandates": {
            "checkout": {"mandate": {}},
            "payment": {"mandate": {"constraints": [{"type": "payment.amount_range", "max": 100}]}}}}
        receipt = {"item": "fixture", "price": 10, "task": "fixture"}
        with mock.patch.object(kit.httpx, "Client", return_value=client), \
             mock.patch.object(kit, "jwt_payload", return_value=receipt), \
             mock.patch.object(kit, "log", side_effect=messages.append):
            result = kit.acp_pay(order, "https://fixture.invalid", "", receipt,
                                 Ed25519PrivateKey.generate(), why=why)
        self.assertIsNone(result)
        self.assertEqual(len(requests), 3)  # no retry or extra market query
        self.assertEqual(len(why), 1)
        return why[0]

    def test_late_failed_check_is_visible_without_raw_payload(self):
        text = self.pay({"error": "PRIVATE" * 100, "market": {"settled": False, "checks": {
            "round_3_order": True, "amount_in_range": False, "PRIVATE_KEY_NAME": False}}})
        self.assertEqual(text, "payment not completed: 402 failed_checks=amount_in_range")
        self.assertNotIn("PRIVATE", text)

    def test_malformed_or_missing_checks_never_leak_or_change_refusal(self):
        for body, raw in [("PRIVATE<html>", True), ([], False), ({"market": None}, False),
                          ({"market": {"checks": ["PRIVATE"]}}, False),
                          ({"market": {"checks": {"PRIVATE": False}}}, False),
                          ({"market": {"checks": {"amount_in_range": "PRIVATE"}}}, False),
                          ({"market": {"checks": {"amount_in_range": 0}}}, False)]:
            with self.subTest(body=type(body).__name__):
                self.assertEqual(self.pay(body, raw), "payment not completed: 402 failed_checks=unavailable")

    def test_deep_json_is_still_a_recorded_refusal_not_an_exception(self):
        self.assertEqual(self.pay("[" * 10000 + "0" + "]" * 10000, raw=True),
                         "payment not completed: 402 failed_checks=unavailable")

    def test_all_known_false_checks_are_sorted_only_boolean_false_counts(self):
        self.assertEqual(self.pay({"market": {"checks": {
            "round_3_order": False, "amount_in_range": False, "not_expired": True}}}),
            "payment not completed: 402 failed_checks=amount_in_range,round_3_order")
