"""A receipt naming only the order's category is not paid, not a mismatch and not held against the shop; everything else is as before."""

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

import httpx

import buyer_funnel
import buyer_support
import market_buyer as market
from r3_selection import select
from test_market_buyer import TradeFixture, shop

ORDER_ITEM = "김치"


def check(receipt, *, order_item=ORDER_ITEM, budget=500, agreed=100):
    full = {"item": ORDER_ITEM, "price": 100, "task": "t", **receipt}
    market.check_receipt(full, item="lamp", budget=budget, task="t", agreed_price=agreed, order_item=order_item)


class CheckRules(unittest.TestCase):
    def kind(self, *args, **kwargs):
        with self.assertRaises(market.ReceiptProblem) as caught:
            check(*args, **kwargs)
        return caught.exception.kind

    def test_exact_order_item_is_its_own_outcome(self):
        self.assertEqual(self.kind({}), "receipt_category_only")

    def test_other_names_stay_mismatches(self):
        for name in ("침구", "김치 ", "김치/반찬", "G00000000", "Lamp"):
            with self.subTest(name):
                self.assertEqual(self.kind({"item": name}), "receipt_mismatch")

    def test_no_order_item_stays_a_mismatch(self):
        for order_item in (None, "", 5):
            with self.subTest(order_item):
                self.assertEqual(self.kind({}, order_item=order_item), "receipt_mismatch")

    def test_price_and_task_problems_stay_mismatches(self):
        self.assertEqual(self.kind({"price": 120}), "receipt_mismatch")
        self.assertEqual(self.kind({"task": "other"}), "receipt_mismatch")
        self.assertEqual(self.kind({}, budget=50), "receipt_mismatch")
        self.assertEqual(self.kind({}, agreed=None), "receipt_mismatch")

    def test_the_sku_receipt_still_passes(self):
        self.assertIsNone(market.check_receipt({"item": "lamp", "price": 100, "task": "t"}, item="lamp", budget=500, task="t", agreed_price=100, order_item=ORDER_ITEM))

    def test_format_labels(self):
        self.assertEqual(market.receipt_format({"item": "lamp"}, "lamp", ORDER_ITEM), "sku")
        self.assertEqual(market.receipt_format({"item": ORDER_ITEM}, "lamp", ORDER_ITEM), "order_item")
        self.assertEqual(market.receipt_format({"item": "x"}, "lamp", ORDER_ITEM), "other")
        self.assertEqual(market.receipt_format({"item": ORDER_ITEM}, "lamp", None), "other")
        self.assertIsNone(market.receipt_format(None, "lamp", ORDER_ITEM))


class Trades(TradeFixture):
    def seller(self, receipt_item, *, cancel="ok"):
        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [
                    {"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            message = body.get("params", {}).get("message", {})
            if body["method"] == "CancelTask":
                if cancel == "ok":
                    state = "TASK_STATE_CANCELED"
                elif cancel == "refused":
                    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32002, "message": "not cancelable"}})
                elif cancel == "down":
                    raise httpx.ConnectError("down")
                else:
                    state = "TASK_STATE_COMPLETED"
            else:
                state = "TASK_STATE_COMPLETED" if message.get("taskId") else "TASK_STATE_INPUT_REQUIRED"
            artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": receipt_item, "price": 110, "task": "task-a", "sig_seller": "fixture-only"}}]}] if state != "TASK_STATE_INPUT_REQUIRED" else []
            task = {"id": "task-a", "contextId": "context-a", "status": {"state": state, "message": {"parts": [{"text": "asking 110."}]}}, "artifacts": artifacts}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": task})
        return httpx.MockTransport(respond)

    def run_trade(self, receipt_item=ORDER_ITEM, order_item=ORDER_ITEM, **seller):
        return market.trade(shop("fixture"), "lamp", 300000, transport=self.seller(receipt_item, **seller), fast_rules=True,
                            model_gate=lambda: False, max_turns=3, order_item=order_item)

    def cancels(self):
        return [p for p in self.posts if p["method"] == "CancelTask"]

    def test_not_paid_not_blamed_and_a_cancel_is_tried(self):
        result, record = self.run_trade()
        self.assertIsNone(result["receipt"])
        self.assertEqual((result["stars"], record["stars"]), (3, 3))
        self.assertIn("no dishonesty inferred", result["review"])
        self.assertEqual(record["outcome"], "receipt_category_only")
        self.assertEqual(record["receipt_format"], "order_item")
        self.assertEqual(record["receipt"]["item"], ORDER_ITEM)
        self.assertEqual(record["failure_reason"], "receipt_category_only")
        self.assertEqual(record["category_receipt_release"]["result"], "canceled")
        self.assertEqual(len(self.cancels()), 1)
        self.assertFalse(record["refused_at_open"])

    def test_a_failed_cancel_changes_nothing_else(self):
        for cancel, expected in (("refused", "failed"), ("down", "failed"), ("stays", "failed")):
            with self.subTest(cancel):
                self.posts.clear()
                result, record = self.run_trade(cancel=cancel)
                self.assertEqual((record["outcome"], result["stars"], result["receipt"]), ("receipt_category_only", 3, None))
                self.assertEqual(record["category_receipt_release"]["result"], expected)
                self.assertEqual(record["receipt"]["item"], ORDER_ITEM)

    def test_other_names_and_skus_stay_mismatches_with_no_cancel(self):
        for name in ("침구", "G00000000"):
            with self.subTest(name):
                self.posts.clear()
                result, record = self.run_trade(receipt_item=name)
                self.assertEqual((record["outcome"], result["stars"], record["receipt_format"]), ("receipt_mismatch", 1, "other"))
                self.assertIsNone(record["category_receipt_release"])
                self.assertEqual(self.cancels(), [])

    def test_no_order_item_is_a_mismatch(self):
        _, record = self.run_trade(order_item=None)
        self.assertEqual(record["outcome"], "receipt_mismatch")

    def test_the_sku_receipt_goes_on_to_payment_as_before(self):
        result, record = self.run_trade(receipt_item="lamp")
        self.assertEqual(result["receipt"]["item"], "lamp")
        self.assertEqual(result["stars"], 5)
        self.assertEqual((record["outcome"], record["receipt_format"]), ("purchased", "sku"))
        self.assertIsNone(record["category_receipt_release"])
        self.assertEqual(self.cancels(), [])


class Consequences(unittest.TestCase):
    NOW = 1793800000.0

    def row(self, **changes):
        at = datetime.fromtimestamp(self.NOW - 60, timezone.utc).isoformat()
        return {"round": 3, "shop_name": "s", "item": "A", "timestamp": at, "agreed_price": 100, "task": "t", "request_format": "sku_only", "decision_trail": [], "refused_at_open": False,
                "outcome": "receipt_category_only", "receipt": {"item": ORDER_ITEM, "price": 100, "task": "t"}, **changes}

    def test_the_shop_stays_a_candidate(self):
        evidence = select([{"name": "s", "url": "http://s.test", "offers": [{"sku": "A", "qty": 3}]}], [self.row()] * 12, {"A": {"list_price": 1000}}, 1000)[4]
        self.assertEqual([e["shop"] for e in evidence], ["s"])
        self.assertFalse(evidence[0]["risk"]["blocked_price_mismatch"])

    def test_a_real_mismatch_after_it_still_blocks(self):
        rows = [self.row(), self.row(outcome="receipt_mismatch", receipt={"item": "B", "price": 100, "task": "t"})]
        self.assertEqual(select([{"name": "s", "url": "http://s.test", "offers": [{"sku": "A", "qty": 3}]}], rows, {"A": {"list_price": 1000}}, 1000)[4], [])

    def test_it_does_not_count_as_a_refusal(self):
        rule = {"refusal_skip": {"enabled": True, "window": 10, "min_share": .9, "retry_seconds": 1800}}
        self.assertEqual(buyer_support.refusing_shops([self.row()] * 12, rule, self.NOW), set())


class MainPassesTheOrderItem(unittest.TestCase):
    def test_wiring(self):
        import inspect
        self.assertIn('"order_item": order.get("item")', inspect.getsource(market.main))


if __name__ == "__main__":
    unittest.main()
