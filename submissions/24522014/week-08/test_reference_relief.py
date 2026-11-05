"""The recent-price cap can be lifted only under every condition, stays inside the order budget and the value margin, and stops itself when it loses."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import buyer_counter
import market_buyer as market
from market_state import read, write
import reference_relief as rr
from test_market_buyer import TradeFixture, shop

COUNTER = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}
GOODS = {"order_budget": 2000, "kind": "goods", "catalog_kind": "goods"}
GOOD = dict(cap=1000, list_price=2000, order_budget=2000, grade="A", kind="goods", catalog_kind="goods", counter_reason="counter_ratio_exceeded")


class Conditions(unittest.TestCase):
    def test_all_conditions_pass(self):
        for reason in rr.ALLOWED_AFTER:
            for grade in ("A", "B"):
                self.assertEqual(rr.eligible(1600, **{**GOOD, "grade": grade, "counter_reason": reason}), (True, None))

    def test_each_condition_refuses_alone(self):
        for changes, why in (({"kind": "food"}, "not_goods"), ({"catalog_kind": "food"}, "not_goods"), ({"grade": None}, "grade_not_a_or_b"), ({"grade": "C"}, "grade_not_a_or_b"),
                             ({"list_price": 1999}, "over_value_limit"), ({"list_price": None}, "over_value_limit"), ({"list_price": True}, "over_value_limit"), ({"order_budget": 1599}, "over_order_budget"),
                             ({"counter_reason": "rule_counter_offer"}, "first_turn_counter_or_other_reason"), ({"counter_reason": "counter_surplus_too_low"}, "first_turn_counter_or_other_reason"),
                             ({"counter_reason": "counter_disabled"}, "first_turn_counter_or_other_reason"), ({"counter_reason": None}, "first_turn_counter_or_other_reason"),
                             ({"cap": 1600}, "not_over_cap")):
            with self.subTest(changes=changes):
                self.assertEqual(rr.eligible(1600, **{**GOOD, **changes}), (False, why))

    def test_value_limit_is_exact_and_ignores_warranty_protection(self):
        self.assertEqual(rr.value_limit(2000), 1600)
        self.assertEqual(rr.value_limit(1999), 1599)
        self.assertEqual(rr.value_limit(1001), 800)
        self.assertEqual(rr.eligible(1601, **GOOD)[1], "over_value_limit")


class Settings(unittest.TestCase):
    def test_off_unless_valid_and_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "s.json"
            self.assertIsNone(rr.settings(path))
            for content in ("{broken", "[]", "{}", '{"reference_relief": 1}', '{"reference_relief": {"enabled": "true", "resume_token": "1"}}', '{"reference_relief": {"enabled": true}}',
                            '{"reference_relief": {"enabled": false, "resume_token": "1"}}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content):
                    self.assertIsNone(rr.settings(path))
            path.write_text('{"reference_relief": {"enabled": true, "resume_token": "1"}}', encoding="utf-8")
            self.assertEqual(rr.settings(path), {"resume_token": "1"})

    def test_shipped_setting_is_off(self):
        self.assertIsNone(rr.settings())
        self.assertIs(json.loads(rr.CONFIG.read_text(encoding="utf-8"))["reference_relief"]["enabled"], False)


class AutoStop(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.md = self.root / ".market"
        self.md.mkdir()
        self.config = self.root / "s.json"
        self.config.write_text('{"reference_relief": {"enabled": true, "resume_token": "1"}}', encoding="utf-8")

    def deal(self, order="o1", token="1"):
        rr.note_deal(self.md, token, order, {"ask": 10}, {"shop_name": "s", "item": "G", "category": "c", "outcome": "purchased"}, now=100)

    def active(self, now=1000):
        return rr.active(self.md, self.root, self.config, now)

    def test_runs_without_deals(self):
        self.assertEqual(self.active(), "1")

    def test_stops_below_zero_says_so_and_stays_off(self):
        self.deal()
        with patch.object(rr, "realized", return_value=(-5, 1, 1, 0)), patch("builtins.print") as said:
            self.assertIsNone(self.active())
        self.assertIn("자동 중지", str(said.call_args))
        self.assertEqual(read(self.md / rr.STATE)["stopped"]["surplus"], -5)
        self.assertEqual(json.loads((self.root / rr.ALERTS).read_text(encoding="utf-8").splitlines()[0])["surplus"], -5)
        with patch.object(rr, "realized", return_value=(99, 1, 0, 0)):
            self.assertIsNone(self.active(now=5000))

    def test_zero_and_above_keep_running(self):
        self.deal()
        for total in (0, 50):
            with self.subTest(total=total), patch.object(rr, "realized", return_value=(total, 1, 0, 0)):
                self.assertEqual(self.active(now=1000 + total * 1000), "1")

    def test_a_new_token_starts_a_new_count(self):
        self.deal()
        with patch.object(rr, "realized", return_value=(-5, 1, 1, 0)), patch("builtins.print"):
            self.assertIsNone(self.active())
        self.config.write_text('{"reference_relief": {"enabled": true, "resume_token": "2"}}', encoding="utf-8")
        with patch.object(rr, "realized") as realized:
            self.assertEqual(self.active(now=5000), "2")
            realized.assert_not_called()

    def test_any_failure_means_off_and_never_raises(self):
        self.deal()
        with patch.object(rr, "realized", side_effect=OSError("disk")):
            self.assertIsNone(self.active())

    def test_a_failed_note_does_not_raise(self):
        with patch.object(rr, "write", side_effect=OSError("disk")), patch("builtins.print"):
            rr.note_deal(self.md, "1", "o", {}, {})

    def test_realized_counts_breakage_and_only_our_orders(self):
        deals = [dict(order="o1", estimated_surplus=-100, broken=True), dict(order="o2", estimated_surplus=30, broken=False), dict(order="other", estimated_surplus=9999, broken=False)]
        with patch("buyer_surplus.settled_deals", return_value=(deals, [])):
            self.assertEqual(rr.realized(self.root, {"o1", "o2"}), (-70, 2, 1, 0))


class Trades(TradeFixture):
    def sequence(self, replies, *, receipt_price=None, metadata=None):
        """Replies in order; an offer after the last one completes the deal at the offered price, or at receipt_price."""
        remaining = list(replies)

        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            message = body.get("params", {}).get("message", {})
            state, artifacts = "TASK_STATE_INPUT_REQUIRED", []
            if body["method"] == "CancelTask":
                state, text = "TASK_STATE_CANCELED", "bye"
            elif remaining:
                text = remaining.pop(0)
            else:
                state, text = "TASK_STATE_COMPLETED", "Deal"
                artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": "lamp", "price": receipt_price or int(message["parts"][0]["text"]), "task": "task-a", "sig_seller": "x"}}]}]
            reply = {"parts": [{"text": text}], "metadata": {"grade": "A", **(metadata or {})}}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "task-a", "contextId": "c", "status": {"state": state, "message": reply}, "artifacts": artifacts}})
        return httpx.MockTransport(respond)

    def trade(self, transport, *, relief=GOODS, cap=1000, list_price=2000, accept=None, gate=None):
        gate = gate or (lambda: False)
        with patch("buyer.run_harness") as harness:
            result, record = market.trade(shop("fixture"), "lamp", cap, transport=transport, fast_rules=True, model_gate=gate, max_turns=3, accept_conditions=accept or {"cert": True, "grades": ["A", "B"]},
                                          over_budget_counter=COUNTER, catalog_list_price=list_price, reference_relief=relief)
            harness.assert_not_called()
        return result, record

    def test_first_turn_counters_then_accepts_the_new_ask(self):
        result, record = self.trade(self.sequence(["Asking 1100.", "Asking 1100."]))
        self.assertEqual(self.offers(), [1000, 1100])
        self.assertEqual(result["receipt"]["price"], 1100)
        self.assertEqual([s["reason_code"] for s in record["decision_trail"]], ["rule_counter_offer", rr.REASON_CODE])
        taken = record["reference_relief"]
        self.assertEqual((taken["ask"], taken["reference_cap"], taken["list_price"], taken["grade"], taken["turn"], taken["counter_reason"]), (1100, 1000, 2000, "A", 2, "counter_already_used"))
        self.assertEqual(record["budget"], 1000)

    def test_a_ratio_too_wide_to_counter_accepts_at_once(self):
        result, record = self.trade(self.sequence(["Asking 1500."]))
        self.assertEqual(self.offers(), [1500])
        self.assertEqual(result["receipt"]["price"], 1500)
        self.assertEqual(record["reference_relief"]["counter_reason"], "counter_ratio_exceeded")

    def test_never_above_the_order_budget_or_the_value_margin(self):
        for replies, relief, list_price in ((["Asking 1500."], {**GOODS, "order_budget": 1499}, 2000), (["Asking 1601."], GOODS, 2000)):
            with self.subTest(replies=replies):
                self.posts.clear()
                result, record = self.trade(self.sequence(replies), relief=relief, list_price=list_price)
                self.assertEqual(self.offers(), [])
                self.assertIsNone(result["receipt"])
                self.assertIsNone(record["reference_relief"])

    def test_a_receipt_other_than_the_accepted_ask_is_refused(self):
        result, record = self.trade(self.sequence(["Asking 1500."], receipt_price=1501))
        self.assertIsNone(result["receipt"])
        self.assertEqual(record["outcome"], "receipt_mismatch")

    def test_food_unknown_grade_and_off_all_stop_at_the_gate(self):
        for relief, metadata in (({**GOODS, "kind": "food"}, None), ({**GOODS, "catalog_kind": "food"}, None), (None, None)):
            with self.subTest(relief=relief):
                self.posts.clear()
                result, record = self.trade(self.sequence(["Asking 1500."], metadata=metadata), relief=relief)
                self.assertEqual(self.offers(), [])
                self.assertEqual(record["decision_trail"][-1]["reason_code"], "model_gate_denied")

    def test_grade_c_is_refused_by_the_condition_even_if_policy_would_allow_it(self):
        result, record = self.trade(self.sequence(["Asking 1500."], metadata={"grade": "C"}), accept={"cert": True, "grades": ["A", "B", "C"]})
        self.assertEqual(self.offers(), [])
        self.assertIsNone(record["reference_relief"])

    def test_no_grade_stops_at_the_gate(self):
        def respond_without_grade(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.posts.append(body)
            state = "TASK_STATE_CANCELED" if body["method"] == "CancelTask" else "TASK_STATE_INPUT_REQUIRED"
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"id": "task-a", "contextId": "c", "status": {"state": state, "message": {"parts": [{"text": "Asking 1500."}]}}, "artifacts": []}})
        result, record = self.trade(httpx.MockTransport(respond_without_grade))
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["decision_trail"][-1]["reason_code"], "model_gate_denied")

    def test_only_once_in_a_negotiation(self):
        result, record = self.trade(self.sequence(["Asking 1500.", "Asking 1590.", "Asking 1590."]))
        self.assertEqual(self.offers()[0], 1500)
        self.assertTrue(all(offer <= 1500 for offer in self.offers()))
        self.assertLessEqual(sum(s["reason_code"] == rr.REASON_CODE for s in record["decision_trail"]), 1)

    def test_the_model_is_not_called_and_the_gate_not_used(self):
        gate = []
        self.trade(self.sequence(["Asking 1500."]), gate=lambda: gate.append(1) or False)
        self.assertEqual(gate, [])


if __name__ == "__main__":
    unittest.main()
