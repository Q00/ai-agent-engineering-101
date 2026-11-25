"""A price under the discount floor must not get paid on any path: selection, rule offer, counter, model, price reading, reference relief, receipt."""

from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import buyer
from buyer_counter import load_settings
import buyer_support
import discount_floor as floor
from market_buyer import ReceiptProblem, check_receipt
from r3_selection import select

SETTINGS = {"floor": .25, "rates": floor.DEFAULT_RATES}
POLICY = {"grades": ["A", "B"], "minimum_surplus_ratio": .05, "uninsured_discount_ratio": .2, "preferred_warranty_hours": 6}
COUNTER = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": .10}


FOLDER = None


def setUpModule():
    global FOLDER
    FOLDER = tempfile.TemporaryDirectory()


def tearDownModule():
    FOLDER.cleanup()


def limit(kind="goods", covered=False, list_price=100000, cap_without_floor=80000):
    return {"list_price": list_price, "kind": kind, "covered": covered, "grades": ["A", "B"], "cap_without_floor": cap_without_floor, "settings": SETTINGS}


class Numbers(unittest.TestCase):
    def test_required_follows_the_three_cases(self):
        p = Fraction("0.098")
        self.assertEqual(floor.required("goods", "B", False, SETTINGS), (Fraction(1, 4)+p)/(1-p))
        p = Fraction("0.111")
        self.assertEqual(floor.required("goods", "B", True, SETTINGS), Fraction(1, 4)/(1-p))
        self.assertEqual(floor.required("food", "C", False, SETTINGS), Fraction(1, 4))

    def test_expected_saved_at_the_ceiling_is_not_under_the_floor(self):
        for grade in "ABC":
            for covered in (False, True):
                need = floor.required("goods", grade, covered, SETTINGS)
                p = Fraction(str(floor.fault_rate(grade, covered, SETTINGS)))
                self.assertEqual((1-p)*need - (0 if covered else p), Fraction(1, 4))

    def test_unknown_grade_is_the_worst_accepted_not_a_refused_one(self):
        self.assertEqual(floor.required("goods", None, False, SETTINGS, ["A", "B"]), floor.required("goods", "B", False, SETTINGS))
        self.assertEqual(floor.required("goods", None, False, SETTINGS), floor.required("goods", "C", False, SETTINGS))
        self.assertEqual(floor.required("goods", "Z", False, SETTINGS, ["A", "B"]), floor.required("goods", "B", False, SETTINGS))

    def test_ceiling_is_exact_at_the_boundary(self):
        self.assertEqual(floor.ceiling(100000, Fraction(1, 4)), 75000)
        self.assertEqual(floor.ceiling(99999, Fraction(1, 4)), 74999)
        self.assertEqual(floor.ceiling(1, Fraction(1, 4)), 0)

    def test_deal_limit_never_raises_the_budget(self):
        self.assertEqual(floor.deal_limit(limit("food"), "A", 60000)[0], 60000)
        self.assertEqual(floor.deal_limit(limit("food"), "A", 90000)[0], 75000)


class Settings(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "s.json"
            if text is not None:
                path.write_text(text, encoding="utf-8")
            return floor.load(path)

    def test_missing_or_broken_never_turns_it_off(self):
        for text in (None, "not json", "[]", "{}", '{"discount_floor": null}', '{"discount_floor": {"floor": null}}', '{"discount_floor": {"floor": 0}}', '{"discount_floor": {"floor": 1}}',
                     '{"discount_floor": {"floor": -0.5}}', '{"discount_floor": {"floor": "0.1"}}', '{"discount_floor": {"floor": true}}', '{"discount_floor": {"enabled": false}}'):
            self.assertEqual(self.load(text), {"floor": .25, "rates": floor.DEFAULT_RATES}, text)

    def test_a_bad_rate_table_uses_the_default_table_and_keeps_a_good_floor(self):
        self.assertEqual(self.load('{"discount_floor": {"floor": 0.3, "fault_rates": {"no_warranty": {"A": 0}}}}'), {"floor": .3, "rates": floor.DEFAULT_RATES})

    def test_the_file_in_this_folder_gives_a_usable_floor(self):
        # 11-08: raised from .25 to .30 for Saved (instruction relayed 11-08, user: "특히 BUYER를 개선해줘").
        self.assertEqual(floor.load()["floor"], .30)


class Selection(unittest.TestCase):
    CATALOG = {"G": {"list_price": 100000, "kind": "goods"}, "F": {"list_price": 100000, "kind": "food"}}

    def cap(self, sku, kind, budget=200000, portfolio=None, policy=POLICY):
        shops = [{"name": "s", "url": "https://s", "offers": [{"sku": sku, "qty": 5}]}]
        with mock.patch("r3_selection.load_floor", return_value=SETTINGS):
            evidence = select(shops, [], self.CATALOG, budget, policy=policy, portfolio=portfolio or [], kind=kind)[4]
        return evidence[0]

    def test_goods_cap_is_under_the_old_twenty_percent_and_at_most_the_best_grade(self):
        row = self.cap("G", "goods")
        self.assertEqual(row["negotiation_cap"], floor.ceiling(100000, floor.required("goods", "A", False, SETTINGS)))
        self.assertEqual(row["cap_without_floor"], 80000)
        self.assertLess(row["negotiation_cap"], 72000)

    def test_food_cap_uses_the_floor_where_the_goods_branch_never_ran(self):
        row = self.cap("F", "food")
        self.assertEqual(row["negotiation_cap"], 75000)
        self.assertEqual(row["cap_without_floor"], 100000)

    def test_warranty_protected_goods_use_the_covered_case(self):
        row = self.cap("G", "goods", portfolio=[{"shop": "https://s", "sku": "G", "warranty_hours": 6, "fault": None}])
        self.assertEqual(row["negotiation_cap"], floor.ceiling(100000, floor.required("goods", "A", True, SETTINGS)))
        self.assertEqual(row["warranty_required"], 6)

    def test_low_budget_still_wins(self):
        self.assertEqual(self.cap("F", "food", budget=50000)["negotiation_cap"], 50000)


class FakeClient:
    """What buyer_decision reads from a transport, with one seller message."""

    def __init__(self, text, budget, discount_limit=None, *, data=None, gate=False):
        self.task = {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": text}] + ([{"data": data}] if data else [])}}}
        self.budget = budget
        self.discount_limit = discount_limit
        self.decisions = []
        self.item = "G"
        self.accept_conditions = {"cert": True, "grades": ["A", "B"]}
        self.price_reading = {"group_a": True, "lowest_price": True, "colon_krw": True}
        self.last_offer = None
        self.rule_decision = lambda message: buyer.classify(message, self.budget, self.last_offer, buyer.seller_metadata(self.task), self.price_reading)[0]
        self.over_budget_counter = dict(COUNTER)
        self.catalog_list_price = (discount_limit or {}).get("list_price")
        self.counter_used = False
        self.model_gate = lambda: gate
        self.model_gate_reason = None
        self.strategy_prompt = Path(__file__).resolve().parent / "prompts/buyer-r3.txt"
        self.log_dir = Path(FOLDER.name)
        self.task_id = "t"
        self.reference_relief = None

    def remaining(self):
        return 100

    def decide(self):
        return buyer.buyer_decision(self, "claude", [], 1, 3)


class RuleAndCounter(unittest.TestCase):
    def test_unstated_grade_uses_the_worst_accepted(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        client = FakeClient(f"Asking price: {need}", 80000, limit())
        self.assertEqual(client.decide(), {"action": "offer", "price": need, "reason": buyer.classify(f"Asking price: {need}", need)[0]["reason"]})
        self.assertEqual(client.budget, need)
        self.assertEqual(client.decisions[-1]["floor_check"]["required"], round(float(floor.required("goods", "B", False, SETTINGS)), 6))

    def test_one_above_the_worst_accepted_is_not_offered_as_asked(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        client = FakeClient(f"Asking price: {need+1}", 80000, limit())
        decision = client.decide()
        self.assertEqual((decision["action"], decision["price"]), ("offer", need))
        self.assertEqual(client.decisions[-1]["reason_code"], "rule_counter_offer")

    def test_stated_grade_a_uses_the_a_ceiling(self):
        top = floor.ceiling(100000, floor.required("goods", "A", False, SETTINGS))
        decision = FakeClient(f"Grade A. Asking price: {top}", 80000, limit()).decide()
        self.assertEqual((decision["action"], decision["price"]), ("offer", top))
        decision = FakeClient(f"Grade A. Asking price: {top+1}", 80000, limit()).decide()
        self.assertEqual(decision["price"], top)

    def test_stated_grade_b_cannot_use_the_a_ceiling(self):
        top = floor.ceiling(100000, floor.required("goods", "A", False, SETTINGS))
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        decision = FakeClient(f"Grade B. Asking price: {top}", 80000, limit()).decide()
        self.assertLessEqual(decision["price"] or 0, need)

    def test_a_lower_grade_later_in_the_deal_keeps_the_lower_limit(self):
        client = FakeClient("Grade B. Asking price: 500000", 80000, limit())
        client.decide()
        lowered = client.budget
        client.task["status"]["message"]["parts"] = [{"text": "Grade A. Asking price: 500000"}]
        client.counter_used = True
        client.decide()
        self.assertEqual(client.budget, lowered)

    def test_food_at_exactly_the_floor_is_offered_and_one_above_is_not(self):
        self.assertEqual(FakeClient("Asking price: 75000", 100000, limit("food")).decide()["price"], 75000)
        decision = FakeClient("Asking price: 75001", 100000, limit("food")).decide()
        self.assertEqual(decision["price"], 75000)

    def test_covered_goods_get_the_higher_ceiling(self):
        top = floor.ceiling(100000, floor.required("goods", "B", True, SETTINGS))
        self.assertEqual(FakeClient(f"Asking price: {top}", 80000, limit(covered=True)).decide()["price"], top)

    def test_refused_by_floor_is_counted_only_when_the_old_cap_would_have_taken_it(self):
        client = FakeClient("Asking price: 79000", 80000, limit())
        client.decide()
        self.assertIs(client.decisions[-1]["floor_check"]["refused_by_floor"], True)
        client = FakeClient("Asking price: 90000", 80000, limit())
        client.decide()
        self.assertIs(client.decisions[-1]["floor_check"]["refused_by_floor"], False)

    def test_counter_uses_the_higher_of_its_own_margin_and_the_required_discount(self):
        client = FakeClient("Asking price: 74000", 73000, limit("food"))
        client.discount_limit = dict(client.discount_limit, settings={"floor": .3, "rates": floor.DEFAULT_RATES})
        client.budget = 90000
        self.assertEqual(client.decide()["price"], 70000)
        self.assertEqual(client.decisions[-1]["counter_check"]["surplus_per_list"], .3)

    def test_counter_off_stays_off(self):
        client = FakeClient("Asking price: 90000", 80000, limit())
        client.over_budget_counter = {"enabled": False, "max_ask_per_cap": None, "min_surplus_per_list": None}
        self.assertEqual(client.decide()["action"], "walk_away")

    def test_the_counter_setting_is_unchanged(self):
        self.assertEqual(load_settings()["max_ask_per_cap"], 1.18)


class PriceReading(unittest.TestCase):
    def test_each_added_reading_stays_under_the_ceiling(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        for text in (f"My price: {need+500}", f"Our final price is {need+500}", f"Our lowest price: {need+500}", f"Price for you: {need+500} KRW"):
            decision = FakeClient(text, 80000, limit()).decide()
            self.assertEqual(decision["price"], need, text)
        client = FakeClient("Here it is.", 80000, limit())
        client.task["status"]["message"]["metadata"] = {"ask": need+500}
        self.assertEqual(client.decide()["price"], need)


class Model(unittest.TestCase):
    def test_a_model_offer_over_the_lowered_budget_is_rejected_after_the_retry(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        client = FakeClient("Hello, this one costs a lot and is 7 years old", 80000, limit(), gate=True)
        answer = {"action": "offer", "price": need+1, "reason": "x"}
        with mock.patch("buyer.run_harness", return_value=answer) as call, mock.patch("buyer.last_model_call", return_value=None):
            with self.assertRaises(buyer.BuyerError):
                client.decide()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(client.decisions[-1]["reason_code"], "model_failed")

    def test_the_model_is_told_the_lowered_budget(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        client = FakeClient("Hello, this one costs a lot and is 7 years old", 80000, limit(), gate=True)
        answer = {"action": "offer", "price": need, "reason": "x"}
        with mock.patch("buyer.run_harness", return_value=answer) as call, mock.patch("buyer.last_model_call", return_value=None):
            self.assertEqual(client.decide()["price"], need)
        self.assertIn(f'"budget": {need}', call.call_args[0][1])


class Relief(unittest.TestCase):
    def setUp(self):
        self.relief = {"order_budget": 100000, "kind": "goods", "catalog_kind": "goods"}

    def test_relief_cannot_lift_the_budget_past_the_floor_ceiling(self):
        # The ask is inside the relief's own 80% limit and over the required discount: without the check it would move the budget.
        client = FakeClient("Grade A. Asking price: 79000", 60000, limit())
        client.reference_relief = self.relief
        client.counter_used = True
        self.assertEqual(client.decide()["action"], "walk_away")
        self.assertEqual(client.budget, 60000)

    def test_relief_under_the_ceiling_still_works_when_someone_turns_it_on(self):
        client = FakeClient("Grade A. Asking price: 70000", 60000, limit())
        client.reference_relief = self.relief
        client.counter_used = True
        self.assertEqual(client.decide()["price"], 70000)


class Receipt(unittest.TestCase):
    def test_a_receipt_over_the_lowered_budget_is_not_passed_on(self):
        need = floor.ceiling(100000, floor.required("goods", "B", False, SETTINGS))
        with self.assertRaises(ReceiptProblem):
            check_receipt({"item": "G", "task": "t", "price": need+1}, item="G", budget=need, task="t", agreed_price=need+1)
        check_receipt({"item": "G", "task": "t", "price": need}, item="G", budget=need, task="t", agreed_price=need)


class Ranking(unittest.TestCase):
    def test_expected_discount_cases(self):
        self.assertEqual(floor.expected_discount(.3, "food", "B", False, SETTINGS), .3)
        self.assertAlmostEqual(floor.expected_discount(.3, "goods", "B", True, SETTINGS), .889*.3)
        self.assertAlmostEqual(floor.expected_discount(.3, "goods", "A", False, SETTINGS), .973*.3-.027)
        self.assertAlmostEqual(floor.expected_discount(.3, "goods", None, False, SETTINGS, ["A", "B"]), .902*.3-.098)

    def test_a_cheap_item_at_a_big_discount_beats_a_costly_one_at_a_small_discount(self):
        catalog = {"BIG": {"list_price": 1000000, "kind": "goods"}, "SMALL": {"list_price": 10000, "kind": "goods"}}
        now = 1000000
        def settled(seq, item, amount):
            return {"round": 3, "kind": "settle", "seq": seq, "ts": now-60, "data": {"shop": f"https://{item}", "item": item, "amount": amount, "grade": "A", "claim_honest": True, "warranty_hours": 0}}
        shops = [{"name": "big", "url": "https://BIG", "offers": [{"sku": "BIG", "qty": 5}]}, {"name": "small", "url": "https://SMALL", "offers": [{"sku": "SMALL", "qty": 5}]}]
        config = {"mode": "balanced", "pair_cooldown_seconds": 300, "reference_max_age_seconds": 3600, "reference_premium_ratio": .02, "alternative_tolerance_ratio": 0, "sufficient_public_samples": 3}
        rows = [settled(1, "BIG", 700000), settled(2, "SMALL", 5000)]
        shop, sku, *_ = buyer_support.choose(shops, [], catalog, 2000000, POLICY, [], rows, {"targets": {}, "rival_names": set(), "rival_urls": set()}, {"attempts": []}, config, now)
        self.assertEqual((shop["name"], sku), ("small", "SMALL"))

    def test_stock_preference_compares_the_same_measure_it_was_given(self):
        best = {"expected_discount": .3, "estimated_surplus": 1, "attributed_faults": 0, "past_settled_price": 1, "listed_qty": 1, "shop": "a", "sku": "x"}
        other = {"expected_discount": .29, "estimated_surplus": 0, "attributed_faults": 0, "past_settled_price": 1, "listed_qty": 9, "shop": "b", "sku": "x"}
        rule = {"enabled": True, "surplus_tolerance_ratio": .05}
        self.assertIs(buyer_support.prefer_listed_stock([best, other], best, rule, "expected_discount"), other)
        self.assertIs(buyer_support.prefer_listed_stock([best, other], best, rule), best)


class Wiring(unittest.TestCase):
    def test_market_buyer_passes_the_limit_on_every_route(self):
        text = (Path(__file__).resolve().parent / "market_buyer.py").read_text(encoding="utf-8")
        start = text.index("trade_options = {")
        self.assertLess(start, text.index('"discount_limit"'))
        self.assertLess(text.index('"discount_limit"'), text.index('if support_decision.get("fast_rules")'))

    def test_settings_file_is_valid_json(self):
        json.loads((Path(__file__).resolve().parent / "buyer_support.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
