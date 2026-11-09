"""Buyer option 1, standing_ask_at_floor: one offer at the seller's clear current ask, at the point the run would walk away from it,
when the ask is within the grade-adjusted floor ceiling and the order budget and the quote passed. Never above either; the counters,
the deal path when the cap is met, and the receipt checks are unchanged; off and the control arm behave exactly as today."""

import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import httpx

import discount_floor
import market_buyer as market
import standing_ask
from test_market_buyer import TradeFixture, shop

COUNTER = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.1}
REPEAT = {"enabled": True, "max_ask_per_cap": 1.5, "max_rule_counters": 3, "min_surplus_per_list": 0.1}
SETTINGS = {"floor": 0.3, "rates": discount_floor.DEFAULT_RATES}


class Seller:
    """A seller that keeps one ask and settles when offered at least that much. `texts` can change what it says per reply."""

    def __init__(self, ask, *, receipt=None, texts=None, settle=True):
        self.ask, self.receipt, self.texts, self.settle = ask, receipt, list(texts or []), settle
        self.offers = []

    def text(self):
        return self.texts.pop(0) if self.texts else f"Grade A, certificate included. Asking price {self.ask}."

    def transport(self):
        def respond(request):
            if request.method == "GET":
                return httpx.Response(200, json={"supportedInterfaces": [{"protocolBinding": "JSONRPC", "protocolVersion": "1.0", "url": "http://fixture.test/rpc"}]})
            body = json.loads(request.content)
            message = body.get("params", {}).get("message", {})
            state, artifacts = "TASK_STATE_INPUT_REQUIRED", []
            text = None
            if body["method"] == "CancelTask":
                state = "TASK_STATE_CANCELED"
            elif message.get("taskId"):
                offered = int(message["parts"][0]["text"])
                self.offers.append(offered)
                if self.settle and offered >= self.ask:
                    state = "TASK_STATE_COMPLETED"
                    artifacts = [{"parts": [{"data": {"shop": "Fixture", "item": "lamp", "price": self.receipt or offered, "task": "task-a", "sig_seller": "fixture-only"}}]}]
            if text is None:
                text = self.text() if state == "TASK_STATE_INPUT_REQUIRED" else "Done."
            task = {"id": "task-a", "contextId": "context-a", "status": {"state": state, "message": {"parts": [{"text": text}]}}, "artifacts": artifacts}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": task})
        return httpx.MockTransport(respond)


class Trades(TradeFixture):
    def run_trade(self, seller, *, cap=60000, list_price=100000, order_budget=90000, on=True, kind="food", accept=None, covered=False):
        limit = {"list_price": list_price, "kind": kind, "covered": covered, "grades": ["A", "B"], "cap_without_floor": cap, "settings": SETTINGS}
        with patch("buyer.run_harness") as harness:
            result, record = market.trade(shop("fixture"), "lamp", cap, transport=seller.transport(), fast_rules=True, model_gate=lambda: False,
                                          max_turns=3, over_budget_counter=COUNTER, counter_repeat=REPEAT, catalog_list_price=list_price,
                                          discount_limit=limit, accept_conditions=accept, standing_ask={"order_budget": order_budget} if on else None)
            harness.assert_not_called()
        return result, record

    def codes(self, record):
        return [e["reason_code"] for e in record["decision_trail"]]

    def test_after_the_last_counter_the_standing_ask_is_taken_once(self):
        seller = Seller(65000)
        result, record = self.run_trade(seller)
        self.assertEqual(seller.offers, [60000, 60000, 60000, 65000])
        self.assertEqual(result["receipt"]["price"], 65000)
        self.assertEqual(record["outcome"], "purchased")
        self.assertEqual(self.codes(record)[-1], standing_ask.REASON_CODE)
        self.assertEqual(record["standing_ask"]["ask"], 65000)
        self.assertEqual(record["standing_ask"]["ceiling"], 70000)
        self.assertEqual(record["standing_ask"]["cap_before"], 60000)

    def test_off_sends_exactly_todays_counters_and_walks_away(self):
        for on in (False,):
            seller = Seller(65000)
            result, record = self.run_trade(seller, on=on)
            self.assertEqual(seller.offers, [60000, 60000, 60000])
            self.assertIsNone(result["receipt"])
            self.assertEqual(self.codes(record)[-1], "rule_counter_exhausted")
            self.assertIsNone(record["standing_ask"])

    def test_the_counters_are_unchanged_before_the_standing_ask(self):
        on, off = Seller(65000), Seller(65000)
        _, a = self.run_trade(on)
        _, b = self.run_trade(off, on=False)
        self.assertEqual(on.offers[:-1], off.offers)
        self.assertEqual(self.codes(a)[:3], self.codes(b)[:3])

    def test_when_the_model_gate_closes_the_standing_ask_is_taken(self):
        seller = Seller(65000)
        result, record = self.run_trade(seller, cap=40000)   # 1.625 x cap: no rule counter fits, so today the gate ends it
        self.assertEqual(seller.offers, [65000])
        self.assertEqual(result["receipt"]["price"], 65000)
        _, off = self.run_trade(Seller(65000), cap=40000, on=False)
        self.assertEqual(self.codes(off)[-1], "model_gate_denied")

    def test_never_above_the_floor_ceiling(self):
        seller = Seller(70001)
        result, record = self.run_trade(seller, cap=60000)
        self.assertTrue(all(o <= 60000 for o in seller.offers))
        self.assertIsNone(result["receipt"])
        self.assertIsNone(record["standing_ask"])
        seller = Seller(70000)
        result, _ = self.run_trade(seller, cap=60000)
        self.assertEqual(result["receipt"]["price"], 70000)

    def test_the_ceiling_is_grade_adjusted_for_goods(self):
        # Goods, grade A, no warranty: need (.3 + .027) / .973, ceiling 66392 of 100000.
        # The grade is read from the quote; with no grade stated the floor takes the worst one the order accepts (B), as today.
        accept = {"cert": True, "grades": ["A", "B"]}
        seller = Seller(66393)
        result, _ = self.run_trade(seller, kind="goods", accept=accept)
        self.assertIsNone(result["receipt"])
        self.assertTrue(all(o <= 60000 for o in seller.offers))
        seller = Seller(66392)
        result, record = self.run_trade(seller, kind="goods", accept=accept)
        self.assertEqual(result["receipt"]["price"], 66392)
        self.assertEqual(record["standing_ask"]["grade"], "A")

    def test_never_above_the_order_budget(self):
        seller = Seller(65000)
        result, record = self.run_trade(seller, order_budget=64999)
        self.assertTrue(all(o <= 60000 for o in seller.offers))
        self.assertIsNone(result["receipt"])
        result, _ = self.run_trade(Seller(65000), order_budget=65000)
        self.assertEqual(result["receipt"]["price"], 65000)

    def test_the_deal_path_is_unchanged_when_the_cap_is_met(self):
        for ask in (58000, 60000):
            with self.subTest(ask=ask):
                on, off = Seller(ask), Seller(ask)
                a, ra = self.run_trade(on)
                b, rb = self.run_trade(off, on=False)
                self.assertEqual(on.offers, off.offers)
                self.assertEqual(on.offers, [ask])
                self.assertEqual(self.codes(ra), self.codes(rb))
                self.assertIsNone(ra["standing_ask"])

    def test_the_receipt_check_still_holds_the_price(self):
        seller = Seller(65000, receipt=65001)
        result, record = self.run_trade(seller)
        self.assertEqual(seller.offers[-1], 65000)
        self.assertIsNone(result["receipt"])
        self.assertEqual(record["outcome"], "receipt_mismatch")

    def test_a_quote_the_accept_checks_refuse_is_never_taken(self):
        texts = ["Grade A, certificate included. Asking price 65000."] * 3 + ["Grade C now. Asking price 65000."]
        seller = Seller(65000, texts=texts)
        result, record = self.run_trade(seller, accept={"cert": True, "grades": ["A", "B"]})
        self.assertNotIn(65000, seller.offers)
        self.assertIsNone(result["receipt"])
        self.assertEqual(self.codes(record)[-1], "rule_counter_exhausted")

    def test_a_seller_that_does_not_settle_at_its_own_ask_gets_a_clean_walk_away(self):
        seller = Seller(65000, settle=False)
        result, record = self.run_trade(seller)
        self.assertEqual(seller.offers, [60000, 60000, 60000, 65000])
        self.assertIsNone(result["receipt"])
        self.assertEqual(record["outcome"], "no_deal")
        self.assertEqual(self.codes(record)[-1], standing_ask.DECLINED_CODE)

    def test_only_once_per_conversation(self):
        seller = Seller(65000, settle=False)
        self.run_trade(seller, cap=40000)
        self.assertEqual(seller.offers.count(65000), 1)

    def test_a_random_sweep_never_pays_above_either_limit(self):
        rng = random.Random(11)
        taken = 0
        for _ in range(300):
            list_price = rng.randrange(5000, 2000000)
            cap = int(list_price * rng.uniform(.45, .7))
            ask = int(list_price * rng.uniform(.5, .95))
            order_budget = int(list_price * rng.uniform(.6, 1.3))
            kind = rng.choice(["food", "goods"])
            seller = Seller(ask)
            result, record = self.run_trade(seller, cap=cap, list_price=list_price, order_budget=order_budget, kind=kind)
            ceiling = discount_floor.ceiling(list_price, discount_floor.required(kind, "A", False, SETTINGS))
            for offer in seller.offers:
                self.assertTrue(offer <= cap or (offer == ask and ask <= ceiling and ask <= order_budget), (offer, cap, ask, ceiling, order_budget))
            if result["receipt"]:
                self.assertLessEqual(result["receipt"]["price"], max(cap, min(ceiling, order_budget)))
                taken += record["standing_ask"] is not None
        self.assertGreater(taken, 20)


class Wiring(unittest.TestCase):
    """trade_attempt passes the standing ask only in the test arm and only on the rule path, with the order budget as its limit."""

    def run_attempt(self, arm, fast_rules=True):
        import argparse
        seen = {}
        def trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": "r"}, {"outcome": "no_deal", "decision_trail": []}
        with tempfile.TemporaryDirectory() as folder:
            md = Path(folder) / ".market"
            md.mkdir()
            args = argparse.Namespace(mode="codex", order=md / "order.json", budget=90000, item="lamp", memory=Path(folder) / "memory.jsonl")
            order = {"order": "o-1", "item": "lamp", "kind": "goods", "budget": 90000}
            pick = {"shop": shop("fixture"), "item": "G1", "accept": {"cert": True, "grades": ["A", "B"]}, "selected": {"cap_without_floor": 60000},
                    "decision": {"max_turns": 3, "fast_rules": fast_rules}, "reason": "r", "budget": 60000}
            with patch.object(market, "trade", side_effect=trade), patch.object(standing_ask, "arm", return_value=arm), patch.object(market, "remember"):
                _, record = market.trade_attempt(args, order, {"G1": {"kind": "goods", "list_price": 100000}}, pick)
        return seen, record

    def test_only_the_test_arm_on_the_rule_path_gets_it(self):
        seen, record = self.run_attempt("standing_ask")
        self.assertEqual(seen["standing_ask"], {"order_budget": 90000})
        self.assertEqual(record["standing_ask_arm"], "standing_ask")
        for arm, fast in (("control", True), (None, True), ("standing_ask", False)):
            with self.subTest(arm=arm, fast=fast):
                seen, record = self.run_attempt(arm, fast)
                self.assertNotIn("standing_ask", seen)
                self.assertEqual(record["standing_ask_arm"], arm)


class Settings(unittest.TestCase):
    def test_off_unless_valid_and_the_arm_is_by_its_own_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "b.json"
            for section in (None, {"enabled": False, "share": .5, "salt": "sa1"}, {"enabled": "true", "share": .5, "salt": "sa1"}, {"enabled": True, "share": 1.0, "salt": "sa1"},
                            {"enabled": True, "share": .5}):
                path.write_text(json.dumps({"standing_ask_at_floor": section}), encoding="utf-8")
                self.assertFalse(standing_ask.load(path)["enabled"], section)
            path.write_text(json.dumps({"standing_ask_at_floor": {"enabled": True, "share": .5, "salt": "sa1"}}), encoding="utf-8")
            on = standing_ask.load(path)
            arms = [standing_ask.arm(f"o-{i:012x}", on) for i in range(4000)]
            self.assertAlmostEqual(arms.count("standing_ask") / 4000, .5, delta=.03)
            self.assertEqual(set(arms), {"standing_ask", "control"})
            self.assertIsNone(standing_ask.arm("o-1", standing_ask.load(Path(folder) / "missing.json")))

    def test_the_shipped_setting(self):
        section = json.loads(Path(standing_ask.ab.CONFIG).read_text(encoding="utf-8"))["standing_ask_at_floor"]
        self.assertEqual((section["share"], section["salt"]), (.8, "sa1"))
        # Installed off at 11:50:26 KST 11-09; switched on in its own commit after the full suite.
        # Switched off 11-09 16:06 KST: a test-arm receipt problem (invalid_receipt) is a stop signal.
        self.assertIs(section["enabled"], False)


if __name__ == "__main__":
    unittest.main()
