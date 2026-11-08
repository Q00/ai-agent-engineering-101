"""Repeated rule counteroffer at the cap: asks up to 1.50 x cap, at most 3 rule counters in one conversation, then a clean walk-away.

It never offers above the cap and never calls a model. The 1.18 first counter (over_budget_counter) is unchanged; off means today's behaviour.
Cap 100 and list price 200 unless a test says otherwise."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import buyer_counter
import market_buyer as market
from test_market_buyer import TradeFixture, shop

FIRST = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}
ON = {"enabled": True, "max_ask_per_cap": 1.5, "max_rule_counters": 3, "min_surplus_per_list": 0.10}
OFF = dict(buyer_counter.REPEAT_DISABLED)


class Settings(unittest.TestCase):
    def load(self, section):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "s.json"
            path.write_text(json.dumps({"rule_counter_repeat": section}), encoding="utf-8")
            return buyer_counter.load_repeat_settings(path)

    def test_valid_settings_load(self):
        self.assertEqual(self.load(ON), ON)

    def test_anything_malformed_is_off(self):
        for bad in ({**ON, "enabled": "true"}, {**ON, "max_ask_per_cap": 1.0}, {**ON, "max_ask_per_cap": 2.5}, {**ON, "max_rule_counters": 0},
                    {**ON, "max_rule_counters": 4}, {**ON, "max_rule_counters": 2.0}, {**ON, "min_surplus_per_list": 1}, {**ON, "max_ask_per_cap": True}, None, []):
            with self.subTest(bad=bad):
                self.assertEqual(self.load(bad), OFF)

    def test_a_missing_file_is_off(self):
        self.assertEqual(buyer_counter.load_repeat_settings(Path(tempfile.gettempdir()) / "no-such-settings.json"), OFF)

    def test_shipped_values_and_the_first_counter_is_untouched(self):
        section = json.loads(buyer_counter.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual({k: section["rule_counter_repeat"][k] for k in ("max_ask_per_cap", "max_rule_counters", "min_surplus_per_list")}, {"max_ask_per_cap": 1.5, "max_rule_counters": 3, "min_surplus_per_list": 0.1})
        self.assertEqual(section["over_budget_counter"], {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.1})


class Replies:
    """A seller that answers each of our offers with the next scripted text, or completes the sale on None."""
    def __init__(self, fixture, quote, replies, metadata=None):
        self.fixture, self.quote, self.replies, self.metadata = fixture, quote, list(replies), metadata

    def transport(self):
        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            self.fixture.posts.append(body)
            message = body.get("params", {}).get("message", {})
            state, text, artifacts = "TASK_STATE_INPUT_REQUIRED", self.quote, []
            if body["method"] == "CancelTask":
                state = "TASK_STATE_CANCELED"
            elif message.get("taskId"):
                offered = int(message["parts"][0]["text"])
                reply = self.replies.pop(0)
                if reply is None:
                    state = "TASK_STATE_COMPLETED"
                    artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": "lamp", "price": offered, "task": "task-a", "sig_seller": "fixture-only"}}]}]
                elif reply == "REJECT":
                    state = "TASK_STATE_REJECTED"
                else:
                    text = reply
            msg = {"parts": [{"text": text}]}
            if self.metadata is not None:
                msg["metadata"] = self.metadata
            task = {"id": "task-a", "contextId": "context-a", "status": {"state": state, "message": msg}, "artifacts": artifacts}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": task})
        return httpx.MockTransport(respond)


class Trades(TradeFixture):
    def setUp(self):
        super().setUp()
        self.gate_calls = []

    def gate(self):
        self.gate_calls.append(1)
        return "model_quota_hour"

    def run_it(self, quote, replies, *, repeat=ON, first=FIRST, list_price=200, cap=100, max_turns=3, gate=None, metadata=None):
        with patch("buyer.run_harness") as harness:
            result, record = market.trade(shop("fixture"), "lamp", cap, transport=Replies(self, quote, replies, metadata).transport(), fast_rules=True,
                                          max_turns=max_turns, model_gate=gate or self.gate, over_budget_counter=first, counter_repeat=repeat,
                                          catalog_list_price=list_price)
            self.harness_calls = harness.call_count
        return result, record

    def codes(self, record):
        return [e["reason_code"] for e in record["decision_trail"]]

    def test_an_ask_over_1_18_up_to_1_50_gets_a_rule_counter_at_the_cap_and_a_sale_at_the_cap(self):
        result, record = self.run_it("asking 150.", [None])
        self.assertEqual(self.offers(), [100])
        self.assertEqual(self.codes(record), ["rule_counter_repeat"])
        self.assertEqual((record["outcome"], result["receipt"]["price"]), ("purchased", 100))
        self.assertEqual((self.gate_calls, self.harness_calls), ([], 0))
        check = record["decision_trail"][0]["counter_repeat_check"]
        self.assertEqual((check["reason_code"], check["ask"], check["cap"], check["offer"], check["rule_counters_before"]), ("rule_counter_offer", 150, 100, 100, 0))

    def test_over_1_50_keeps_todays_path_to_the_gate(self):
        _, record = self.run_it("asking 151.", [])
        self.assertEqual(self.offers(), [])
        self.assertEqual(self.gate_calls, [1])
        self.assertEqual(record["failure_reason"], "model_quota_hour")
        self.assertEqual(record["decision_trail"][0]["counter_repeat_check"]["reason_code"], "counter_ratio_exceeded")

    def test_three_counters_at_most_then_a_clean_walk_away_without_a_model(self):
        _, record = self.run_it("asking 110.", ["Counteroffer: 108.", "Counteroffer: 107.", "Counteroffer: 106."])
        self.assertEqual(self.offers(), [100, 100, 100])
        self.assertEqual(self.codes(record), ["rule_counter_offer", "rule_counter_repeat", "rule_counter_repeat", "rule_counter_exhausted"])
        self.assertEqual((self.gate_calls, self.harness_calls), ([], 0))
        self.assertEqual((record["outcome"], record["negotiation_outcome"], record["error"]), ("no_deal", "walked_away", None))
        self.assertEqual(self.posts[-1]["method"], "CancelTask")
        self.assertFalse(record["refused_at_open"])

    def test_the_count_includes_the_first_counter_and_stops_inside_the_turn_limit(self):
        _, record = self.run_it("asking 110.", ["Counteroffer: 108.", "Counteroffer: 107."], max_turns=6, repeat={**ON, "max_rule_counters": 2})
        self.assertEqual(self.offers(), [100, 100])
        self.assertEqual(self.codes(record), ["rule_counter_offer", "rule_counter_repeat", "rule_counter_exhausted"])
        self.assertEqual(self.gate_calls, [])

    def test_a_later_reply_within_the_cap_is_bought_by_the_existing_rule(self):
        result, record = self.run_it("asking 140.", ["Counteroffer: 120.", "Counteroffer: 99.", None])
        self.assertEqual(self.offers(), [100, 100, 99])
        self.assertEqual(self.codes(record), ["rule_counter_repeat", "rule_counter_repeat", "rule_offer"])
        self.assertEqual(result["receipt"]["price"], 99)

    def test_a_seller_raising_the_ask_past_1_50_goes_to_the_gate(self):
        _, record = self.run_it("asking 110.", ["Counteroffer: 160."])
        self.assertEqual(self.offers(), [100])
        self.assertEqual(self.codes(record), ["rule_counter_offer", "model_quota_hour"])
        self.assertEqual(self.gate_calls, [1])

    def test_an_unreadable_reply_still_goes_to_the_gate(self):
        _, record = self.run_it("asking 110.", ["100 is our lowest: this is already a clearance price."])
        self.assertEqual(self.codes(record), ["rule_counter_offer", "model_quota_hour"])
        self.assertNotIn("counter_repeat_check", record["decision_trail"][1])

    def test_the_floor_and_surplus_still_hold(self):
        # Cap 91 against list 100 leaves .09 of list, under the .10 minimum: no counter, today's gate.
        _, record = self.run_it("asking 120.", [], cap=91, list_price=100)
        self.assertEqual(self.offers(), [])
        self.assertEqual(record["decision_trail"][0]["counter_repeat_check"]["reason_code"], "counter_surplus_too_low")
        _, record = self.run_it("asking 120.", [], list_price=None)
        self.assertEqual(record["decision_trail"][0]["counter_repeat_check"]["reason_code"], "counter_no_list_price")

    def test_a_seller_rejecting_a_repeat_is_a_plain_no_deal_and_not_a_refusal_at_open(self):
        _, record = self.run_it("asking 110.", ["Counteroffer: 108.", "REJECT"])
        self.assertEqual(record["failure_reason"], "seller_rejected")
        self.assertFalse(record["refused_at_open"])

    def test_off_behaves_exactly_like_today(self):
        for quote, replies in (("asking 150.", []), ("asking 110.", ["Counteroffer: 108."]), ("asking 110.", [None]), ("asking 90.", [None]), ("asking 151.", [])):
            with self.subTest(quote=quote, replies=replies):
                self.posts.clear(); self.gate_calls.clear()
                today, record_today = self.run_it(quote, list(replies), repeat=None)
                offers_today, gate_today = self.offers(), list(self.gate_calls)
                self.posts.clear(); self.gate_calls.clear()
                off, record_off = self.run_it(quote, list(replies), repeat=OFF)
                self.assertEqual((self.offers(), self.gate_calls), (offers_today, gate_today))
                self.assertEqual(self.codes(record_off), self.codes(record_today))
                self.assertEqual(off["receipt"], today["receipt"])
                self.assertNotIn("counter_repeat_check", json.dumps(record_off["decision_trail"]))

    def test_never_above_the_cap_even_when_the_seller_names_a_lower_price_than_its_ask(self):
        result, record = self.run_it("asking 149.", ["Counteroffer: 101.", None])
        self.assertTrue(all(price <= 100 for price in self.offers()))
        self.assertEqual(result["receipt"]["price"], 100)


class Wiring(unittest.TestCase):
    def test_market_buyer_passes_the_shipped_settings(self):
        source = Path(market.__file__).read_text(encoding="utf-8")
        self.assertIn("counter_repeat=load_repeat_settings()", source)


if __name__ == "__main__":
    unittest.main()
