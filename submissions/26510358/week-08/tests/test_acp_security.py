"""Synthetic pre-payment checks; no network, signatures or credit transfers."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acp_security import AcpValidationError, validate_checkout, validate_endpoint


def contract():
    receipt = {"shop": "TEST SHOP", "item": "lamp", "price": 100, "task": "task-1"}
    payload = {**receipt, "id": "cs_test-1", "shop_url": "https://shop.test/store", "currency": "credits", "iat": 1}
    session = {"id": payload["id"], "status": "ready_for_payment", "task": receipt["task"],
               "line_items": [{"id": "lamp", "quantity": 1}],
               "totals": [{"type": "total", "amount": 100}],
               "capabilities": {"payment_methods": ["ap2-mandate"]}}
    order = {"order": "order-1", "item": "lamp", "budget": 120,
             "mandates": {"payment": {"mandate": {"constraints": [
                 {"type": "payment.amount_range", "min": 0, "max": 120}]}}}}
    return order, receipt, session, payload


class EndpointChecks(unittest.TestCase):
    def test_https_host_case_and_default_port_normalize(self):
        self.assertEqual(validate_endpoint("https://SHOP.test:443/store/", "https://shop.test/acp/"),
                         "https://shop.test/acp")

    def test_different_host_scheme_or_effective_port_is_rejected(self):
        for endpoint in ("https://evil.test/acp", "http://shop.test/acp", "https://shop.test:444/acp"):
            with self.subTest(endpoint=endpoint), self.assertRaises(AcpValidationError):
                validate_endpoint("https://shop.test/store", endpoint)

    def test_credentials_query_fragment_and_relative_urls_are_rejected(self):
        for endpoint in ("https://user:secret@shop.test/acp", "https://shop.test/acp?token=secret",
                         "https://shop.test/acp#fragment", "https://shop.test/acp?", "//shop.test/acp", "/acp"):
            with self.subTest(endpoint=endpoint), self.assertRaises(AcpValidationError):
                validate_endpoint("https://shop.test", endpoint)

    def test_whitespace_backslash_bad_ports_and_encoded_traversal_are_rejected(self):
        for endpoint in (" https://shop.test/acp", "https://shop.test/acp\n", "https://shop.test\\evil/acp",
                         "https://shop.test:0/acp", "https://shop.test:65536/acp", "https://shop.test:/acp",
                         "https://shop.test/../acp", "https://shop.test/%2e%2e/acp", "https://shop.test/x%2facp",
                         "https://shop.test/%252e%252e/acp", "https://shop.test./acp"):
            with self.subTest(endpoint=endpoint), self.assertRaises(AcpValidationError):
                validate_endpoint("https://shop.test", endpoint)

    def test_local_http_requires_explicit_opt_in_and_same_port(self):
        with self.assertRaises(AcpValidationError):
            validate_endpoint("http://127.0.0.1:8080", "http://127.0.0.1:8080/acp")
        for host in ("127.0.0.1", "localhost", "[::1]"):
            base = "http://" + host + ":8080"
            self.assertEqual(validate_endpoint(base, base + "/acp", allow_local_http=True), base + "/acp")
        with self.assertRaises(AcpValidationError):
            validate_endpoint("http://127.0.0.1:8080", "http://127.0.0.1:8081/acp", allow_local_http=True)
        with self.assertRaises(AcpValidationError):
            validate_endpoint("http://shop.test", "http://shop.test/acp", allow_local_http=True)


class CheckoutChecks(unittest.TestCase):
    def check(self, order=None, receipt=None, session=None, payload=None, url="https://shop.test/store"):
        baseline = contract()
        return validate_checkout(order if order is not None else baseline[0], url,
                                 receipt if receipt is not None else baseline[1],
                                 session if session is not None else baseline[2],
                                 payload if payload is not None else baseline[3])

    def assert_code(self, code, **kwargs):
        with self.assertRaises(AcpValidationError) as error:
            self.check(**kwargs)
        self.assertEqual(error.exception.code, code)
        self.assertEqual(str(error.exception), code)

    def test_valid_official_shape_returns_integer_total(self):
        self.assertEqual(self.check(), 100)

    def test_integer_valued_a2a_double_is_accepted(self):
        order, receipt, session, payload = contract()
        receipt["price"] = payload["price"] = session["totals"][0]["amount"] = 100.0
        self.assertIs(type(self.check(receipt=receipt, session=session, payload=payload)), int)

    def test_boolean_fraction_negative_nonfinite_and_unsafe_float_prices_are_rejected(self):
        for price in (True, False, 0, -1, 100.5, float("nan"), float("inf"), float(2**53)):
            with self.subTest(price=price):
                _, receipt, _, _ = contract()
                receipt["price"] = price
                self.assert_code("invalid_receipt_price", receipt=receipt)

    def test_registered_shop_identity_and_full_trusted_shop_url_must_match(self):
        for field, value, code in (("shop", "OTHER", "checkout_shop_mismatch"),
                                   ("shop_url", "https://shop.test/other-store", "checkout_shop_url_mismatch"),
                                   ("shop_url", "https://shop.test:444/store", "checkout_shop_url_mismatch")):
            _, _, _, payload = contract()
            payload[field] = value
            self.assert_code(code, payload=payload)

    def test_checkout_contract_and_currency_must_match(self):
        for field, value, code in (("item", "chair", "checkout_contract_mismatch"),
                                   ("task", "replayed-task", "checkout_contract_mismatch"),
                                   ("price", 101, "checkout_price_mismatch"),
                                   ("price", True, "invalid_checkout_price"),
                                   ("currency", "USD", "checkout_currency_mismatch"),
                                   ("id", "cs_other", "checkout_session_mismatch")):
            _, _, _, payload = contract()
            payload[field] = value
            self.assert_code(code, payload=payload)

    def test_completed_canceled_and_unknown_sessions_cannot_be_completed(self):
        for status in ("completed", "canceled", "unknown", None):
            _, _, session, _ = contract()
            session["status"] = status
            self.assert_code("session_not_ready", session=session)

    def test_session_id_is_a_safe_single_path_segment_and_signed_id_must_match(self):
        for sid in ("../other", "cs_1/complete", "cs_1?secret=1", "", True):
            _, _, session, _ = contract()
            session["id"] = sid
            self.assert_code("invalid_session_id", session=session)

    def test_line_items_require_exact_item_and_single_integer_quantity(self):
        for items in ([], [{"id": "chair", "quantity": 1}], [{"id": "lamp", "quantity": True}],
                      [{"id": "lamp", "quantity": 1.0}], [{"id": "lamp", "quantity": 2}],
                      [{"id": "lamp", "quantity": 1}] * 2):
            _, _, session, _ = contract()
            session["line_items"] = items
            self.assert_code("session_line_items_mismatch", session=session)

    def test_totals_cannot_hide_extra_line_or_disagree_with_signed_price(self):
        for totals, code in (([], "invalid_session_totals"),
                             ([{"type": "subtotal", "amount": 100}], "invalid_session_totals"),
                             ([{"type": "total", "amount": 100}] * 2, "invalid_session_totals"),
                             ([{"type": "total", "amount": 101}], "session_total_mismatch"),
                             ([{"type": "total", "amount": True}], "invalid_session_total")):
            _, _, session, _ = contract()
            session["totals"] = totals
            self.assert_code(code, session=session)

    def test_session_task_and_supported_payment_method_are_required(self):
        _, _, session, _ = contract()
        session["task"] = "other"
        self.assert_code("session_task_mismatch", session=session)
        for capabilities in (None, {}, {"payment_methods": "ap2-mandate"}, {"payment_methods": ["other"]}):
            _, _, session, _ = contract()
            session["capabilities"] = capabilities
            self.assert_code("unsupported_payment_method", session=session)

    def test_order_item_budget_and_payment_limit_are_independent_gates(self):
        order, _, _, _ = contract()
        order["item"] = "chair"
        self.assert_code("order_item_mismatch", order=order)
        order, _, _, _ = contract()
        order["budget"] = 99
        self.assert_code("order_budget_exceeded", order=order)
        order, _, _, _ = contract()
        order["mandates"]["payment"]["mandate"]["constraints"][0]["max"] = 99
        self.assert_code("payment_amount_out_of_range", order=order)

    def test_malformed_or_ambiguous_payment_ranges_fail_closed(self):
        for constraints, code in ((None, "invalid_payment_constraints"),
                                  ([], "invalid_payment_amount_range"),
                                  ([{"type": "payment.amount_range", "max": 120}] * 2, "invalid_payment_amount_range"),
                                  ([{"type": "payment.amount_range", "max": True}], "invalid_payment_amount_max"),
                                  ([{"type": "payment.amount_range", "max": 120, "min": 101}], "payment_amount_out_of_range"),
                                  ([{"type": "payment.amount_range", "max": 120, "currency": "USD"}], "payment_currency_mismatch")):
            order, _, _, _ = contract()
            order["mandates"]["payment"]["mandate"]["constraints"] = constraints
            self.assert_code(code, order=order)

    def test_missing_signature_verified_payload_is_not_accepted(self):
        self.assert_code("invalid_checkout_shape", payload=False)

    def test_validations_do_not_mutate_inputs_or_expose_private_mandates(self):
        order, receipt, session, payload = contract()
        order["mandates"]["payment"]["mandate"]["private"] = "DO_NOT_EXPOSE"
        original = deepcopy((order, receipt, session, payload))
        self.check(order=order, receipt=receipt, session=session, payload=payload)
        self.assertEqual((order, receipt, session, payload), original)
        order["budget"] = 99
        with self.assertRaises(AcpValidationError) as error:
            self.check(order=order)
        self.assertNotIn("DO_NOT_EXPOSE", str(error.exception))


if __name__ == "__main__":
    unittest.main()
