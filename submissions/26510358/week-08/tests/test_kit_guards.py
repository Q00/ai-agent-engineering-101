"""TEST ONLY: signed fixture checkouts, mocked HTTP and no Market activity."""
import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

WEEK08 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEEK08))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_acp_security import contract

# Import the patched kit separately from the shared official module used by
# market_runtime tests. No command line entry point or process is started.
SPEC = importlib.util.spec_from_file_location("week08_guarded_kit", WEEK08 / "market_kit.py")
kit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kit
SPEC.loader.exec_module(kit)


class PaymentGuards(unittest.TestCase):
    def setUp(self):
        self.order, self.receipt, self.session, self.payload = contract()
        self.order["mandates"]["checkout"] = {"mandate": {"vct": "mandate.checkout.open.1", "constraints": []}}
        self.shop_url = self.payload["shop_url"]
        self.shop_key = Ed25519PrivateKey.generate()
        self.buyer_key = Ed25519PrivateKey.generate()

    def run_payment(self, *, endpoint="https://shop.test/acp", forged=False, timeout=False,
                    descriptor_status=200):
        session = deepcopy(self.session)
        signing_key = Ed25519PrivateKey.generate() if forged else self.shop_key
        session["checkout_jwt"] = kit.jwt_sign(signing_key, self.payload)
        client = Mock(spec=["get", "post", "close"])
        client.get.return_value = httpx.Response(descriptor_status, json={"endpoint": endpoint})
        completed = httpx.Response(200, json={"order": {"mandate_receipt": {"ledger": 987, "order": "order-1"}}})
        result = httpx.ReadTimeout("TEST ONLY PRIVATE MUST NOT LEAK") if timeout else completed
        client.post.side_effect = [httpx.Response(201, json=session), result]
        with patch.object(kit.httpx, "Client", return_value=client) as factory, patch.object(kit, "log") as log:
            receipt = kit.acp_pay(self.order, self.shop_url, kit.pub_b64(self.shop_key), self.receipt, self.buyer_key)
        return receipt, client, factory, log

    def complete_calls(self, client):
        return [call for call in client.post.call_args_list if call.args[0].endswith("/complete")]

    def test_valid_signature_and_matching_session_complete_exactly_once(self):
        result, client, factory, _ = self.run_payment()
        self.assertEqual(result, {"ledger": 987, "order": "order-1"})
        self.assertEqual(len(client.post.call_args_list), 2)
        calls = self.complete_calls(client)
        self.assertEqual(len(calls), 1)
        factory.assert_called_once_with(timeout=60, follow_redirects=False, trust_env=False)
        client.close.assert_called_once_with()
        payment_data = calls[0].kwargs["json"]["payment_data"]
        self.assertEqual(payment_data["order"], self.order["order"])
        self.assertEqual(payment_data["payment_mandate"]["mandate"]["amount"], 100)
        for name in ("checkout_mandate", "payment_mandate"):
            signed = payment_data[name]
            self.buyer_key.public_key().verify(base64.b64decode(signed["sig"]), kit.canon(signed["mandate"]))
        self.assertEqual(payment_data["checkout_mandate"]["mandate"]["checkout_hash"],
                         payment_data["payment_mandate"]["mandate"]["checkout_hash"])

    def test_downgrade_or_different_port_descriptor_never_posts(self):
        for endpoint in ("http://shop.test/acp", "https://shop.test:444/acp", "https://other.test/acp"):
            with self.subTest(endpoint=endpoint):
                result, client, _, _ = self.run_payment(endpoint=endpoint)
                self.assertIsNone(result)
                client.post.assert_not_called()

    def test_redirect_descriptor_is_not_followed_and_never_posts(self):
        result, client, _, _ = self.run_payment(descriptor_status=302)
        self.assertIsNone(result)
        client.post.assert_not_called()
        self.assertEqual(client.get.call_count, 1)

    def test_valid_signature_with_altered_visible_total_never_completes(self):
        self.session["totals"][0]["amount"] = 101
        result, client, _, _ = self.run_payment()
        self.assertIsNone(result)
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(self.complete_calls(client), [])

    def test_forged_checkout_signature_never_completes(self):
        result, client, _, _ = self.run_payment(forged=True)
        self.assertIsNone(result)
        self.assertEqual(client.post.call_count, 1)
        self.assertEqual(self.complete_calls(client), [])

    def test_consistent_signed_price_over_order_budget_never_completes(self):
        self.receipt["price"] = self.payload["price"] = self.session["totals"][0]["amount"] = 121
        result, client, _, _ = self.run_payment()
        self.assertIsNone(result)
        self.assertEqual(self.complete_calls(client), [])

    def test_already_completed_session_never_completes_again(self):
        self.session["status"] = "completed"
        result, client, _, _ = self.run_payment()
        self.assertIsNone(result)
        self.assertEqual(self.complete_calls(client), [])

    def test_complete_timeout_is_not_retried_and_error_content_is_not_logged(self):
        result, client, _, log = self.run_payment(timeout=True)
        self.assertIsNone(result)
        self.assertEqual(client.post.call_count, 2)
        self.assertEqual(len(self.complete_calls(client)), 1)
        self.assertNotIn("PRIVATE MUST NOT LEAK", str(log.call_args_list))
        self.assertIn("reconciliation", str(log.call_args_list))

    def test_nonobject_create_response_fails_locally_without_completing(self):
        for body in (None, [], "not a session"):
            with self.subTest(body=body):
                client = Mock(spec=["get", "post", "close"])
                client.get.return_value = httpx.Response(200, json={"endpoint": "https://shop.test/acp"})
                client.post.return_value = httpx.Response(201, content=json.dumps(body))
                with patch.object(kit.httpx, "Client", return_value=client), patch.object(kit, "log"):
                    result = kit.acp_pay(self.order, self.shop_url, kit.pub_b64(self.shop_key), self.receipt, self.buyer_key)
                self.assertIsNone(result)
                self.assertEqual(client.post.call_count, 1)

    def test_loopback_http_requires_explicit_local_demo_permission(self):
        client = Mock(spec=["get", "post", "close"])
        with patch.object(kit.httpx, "Client", return_value=client), patch.object(kit, "log"):
            result = kit.acp_pay(self.order, "http://127.0.0.1:9000", kit.pub_b64(self.shop_key),
                                 self.receipt, self.buyer_key)
        self.assertIsNone(result)
        client.get.assert_not_called()


class ResultBoundaryGuards(unittest.TestCase):
    def setUp(self):
        private = WEEK08 / ".market"
        private.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=private)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.shop_key = Ed25519PrivateKey.generate()
        self.buyer_key = Ed25519PrivateKey.generate()
        self.other_key = Ed25519PrivateKey.generate()
        self.own_url, self.other_url = "https://own.test", "https://other.test"

    def run_order(self, *, round=3, same_owner=False, same_key=False, stars=5,
                  valid_signature=True, mandates=True, settled=True, receipt_present=True):
        order, receipt, _, _ = contract()
        order["round"] = round
        if not mandates:
            order.pop("mandates")
        signing_key = self.shop_key if same_key else self.other_key
        receipt["sig_seller"] = base64.b64encode(signing_key.sign(kit.canon(receipt))).decode()
        if not valid_signature:
            receipt["price"] = 101
        selected_key = kit.pub_b64(self.shop_key if same_key else self.other_key)
        shops = [{"name": "other", "url": self.other_url, "pubkey": selected_key, "reviews": []}]
        board = {"buyers": [{"name": "buyer", "owner": {"login": "owner"}}],
                 "shops": [{"name": "other", "owner": {"login": "owner" if same_owner else "other-owner"},
                            "card": {"url": self.other_url}}]}
        market = SimpleNamespace(joined={"buyer": ("buyer", self.buyer_key, self.own_url)},
            call=Mock(side_effect=lambda method, path, **kwargs: httpx.Response(
                200, json={"shops": shops} if path == "/api/shops" else board,
                request=httpx.Request(method, "https://market.test" + path))))
        output = {"shop": self.other_url, "receipt": receipt if receipt_present else None,
                  "stars": stars, "review": "TEST ONLY transaction observation"}
        response = httpx.Response(200, json={"checks": {}, "ledger": 123,
                                            "verified": settled, "review_accepted": False})
        with patch.object(kit, "run_buyer", return_value=[json.dumps(output)]), \
                patch.object(kit, "post_result", return_value=response) as posted, \
                patch.object(kit, "acp_pay", return_value={"ledger": 122} if settled else None) as paid, \
                patch.object(kit, "log"):
            kit.handle_order(order, {"market_buyer": {"cmd": "TEST ONLY {shops} {item} {budget}"}},
                             self.directory, market, None, self.own_url,
                             {"shop": self.shop_key, "buyer": self.buyer_key})
        self.assertEqual(posted.call_count, 1)
        return posted.call_args.args[1], paid

    def test_valid_eligible_receipt_is_signed_and_paid_once(self):
        body, paid = self.run_order()
        self.assertIn("sig_buyer", body)
        self.assertEqual(body["stars"], 5)
        self.assertEqual(body["mandate_receipt"], {"ledger": 122})
        paid.assert_called_once()

    def test_same_owner_url_returned_by_buyer_cannot_be_signed_or_paid(self):
        body, paid = self.run_order(same_owner=True)
        self.assertNotIn("sig_buyer", body)
        self.assertIsNone(body.get("receipt"))
        self.assertIsNone(body.get("stars"))
        paid.assert_not_called()

    def test_same_key_alias_returned_by_buyer_cannot_be_signed_or_paid(self):
        body, paid = self.run_order(same_key=True)
        self.assertNotIn("sig_buyer", body)
        self.assertIsNone(body.get("receipt"))
        paid.assert_not_called()

    def test_nullable_stars_remain_nullable_for_a_valid_round2_receipt(self):
        body, paid = self.run_order(round=2, stars=None)
        self.assertIn("sig_buyer", body)
        self.assertIsNone(body["stars"])
        paid.assert_not_called()

    def test_invalid_signature_never_pays_or_publishes_success_review(self):
        body, paid = self.run_order(valid_signature=False)
        self.assertNotIn("sig_buyer", body)
        self.assertIsNone(body["stars"])
        self.assertEqual(body["review"], "")
        paid.assert_not_called()

    def test_missing_round3_settlement_suppresses_prepared_review(self):
        body, paid = self.run_order(settled=False)
        self.assertIn("sig_buyer", body)
        self.assertIsNone(body["mandate_receipt"])
        self.assertIsNone(body["stars"])
        self.assertEqual(body["review"], "")
        paid.assert_called_once()

    def test_round3_order_without_mandates_never_pays_or_publishes_success_review(self):
        body, paid = self.run_order(mandates=False)
        self.assertIsNone(body["stars"])
        self.assertEqual(body["review"], "")
        self.assertNotIn("mandate_receipt", body)
        paid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
