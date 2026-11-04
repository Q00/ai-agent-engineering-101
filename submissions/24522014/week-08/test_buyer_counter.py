"""The over-budget counteroffer stays inside the cap, off by default, and explains every exclusion."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import buyer_counter as bc

ON = {"enabled": True, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}


class Evaluate(unittest.TestCase):
    def run_it(self, ask=118, cap=100, list_price=200, used=False, last_offer=None, config=ON):
        return bc.evaluate(ask, cap, list_price, used=used, last_offer=last_offer, config=config)

    def test_offers_the_cap_as_an_integer_and_reports_the_numbers(self):
        decision, check = self.run_it(ask=110)
        self.assertEqual(decision["action"], "offer")
        self.assertEqual(decision["price"], 100)
        self.assertIs(type(decision["price"]), int)
        self.assertEqual(check["reason_code"], "rule_counter_offer")
        self.assertEqual((check["ask"], check["cap"], check["list_price"], check["offer"]), (110, 100, 200, 100))
        self.assertEqual(check["ask_per_cap"], 1.1)
        self.assertEqual(check["surplus_per_list"], 0.5)

    def test_ratio_boundary_is_exact(self):
        self.assertEqual(self.run_it(ask=118)[1]["reason_code"], "rule_counter_offer")
        self.assertEqual(self.run_it(ask=119)[1]["reason_code"], "counter_ratio_exceeded")
        # Binary floating point must not move these boundaries.
        for cap in (50, 100, 150, 1000, 12345, 178698, 33333):
            limit = cap * 118 // 100
            with self.subTest(cap=cap):
                self.assertEqual(self.run_it(ask=limit, cap=cap, list_price=cap * 2)[1]["reason_code"], "rule_counter_offer")
                self.assertEqual(self.run_it(ask=limit + 1, cap=cap, list_price=cap * 2)[1]["reason_code"], "counter_ratio_exceeded")

    def test_surplus_boundary_is_exact(self):
        self.assertEqual(self.run_it(cap=90, ask=100, list_price=100)[1]["reason_code"], "rule_counter_offer")
        self.assertEqual(self.run_it(cap=91, ask=100, list_price=100)[1]["reason_code"], "counter_surplus_too_low")
        self.assertEqual(self.run_it(cap=900, ask=1000, list_price=1000)[1]["reason_code"], "rule_counter_offer")
        self.assertEqual(self.run_it(cap=901, ask=1000, list_price=1000)[1]["reason_code"], "counter_surplus_too_low")

    def test_offer_above_list_price_is_excluded_without_a_negative_pass(self):
        decision, check = self.run_it(cap=300, ask=310, list_price=200)
        self.assertIsNone(decision)
        self.assertEqual(check["reason_code"], "counter_surplus_too_low")

    def test_unknown_list_price_never_offers(self):
        for value in (None, 0, -1, "200", True, float("nan"), float("inf")):
            with self.subTest(value=value):
                decision, check = self.run_it(list_price=value)
                self.assertIsNone(decision)
                self.assertEqual(check["reason_code"], "counter_no_list_price")
                self.assertIsNone(check["list_price"])

    def test_only_once_per_negotiation(self):
        decision, check = self.run_it(used=True)
        self.assertIsNone(decision)
        self.assertEqual(check["reason_code"], "counter_already_used")

    def test_an_offer_already_made_at_the_cap_is_not_repeated(self):
        decision, check = self.run_it(last_offer=100)
        self.assertIsNone(decision)
        self.assertEqual(check["reason_code"], "counter_repeated_price")

    def test_disabled_takes_priority_and_still_records_the_numbers(self):
        for config in ({"enabled": False, "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.10}, bc.DISABLED):
            with self.subTest(config=config):
                decision, check = self.run_it(ask=500, used=True, list_price=None, config=config)
                self.assertIsNone(decision)
                self.assertEqual(check["reason_code"], "counter_disabled")
                self.assertEqual((check["ask"], check["cap"]), (500, 100))

    def test_inputs_outside_over_budget_are_never_countered(self):
        for ask, cap in ((100, 100), (99, 100), (None, 100), (0, 100), (101, 0), (101, None), (101.5, 100), (True, 100)):
            with self.subTest(ask=ask, cap=cap):
                decision, check = self.run_it(ask=ask, cap=cap)
                self.assertIsNone(decision)
                self.assertEqual(check["reason_code"], "counter_not_over_budget")

    def test_an_invalid_config_passed_directly_is_disabled(self):
        for config in (None, {}, {"enabled": "true", "max_ask_per_cap": 1.18, "min_surplus_per_list": 0.1},
                       {"enabled": True}, {"enabled": True, "max_ask_per_cap": 3, "min_surplus_per_list": 0.1}):
            with self.subTest(config=config):
                self.assertEqual(self.run_it(config=config)[1]["reason_code"], "counter_disabled")


class Settings(unittest.TestCase):
    def load(self, content):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "buyer_support.json"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            return bc.load_settings(path)

    def test_valid_enabled_settings_load(self):
        self.assertEqual(self.load(json.dumps({"over_budget_counter": ON})), ON)

    def test_valid_disabled_settings_load_as_disabled(self):
        loaded = self.load(json.dumps({"over_budget_counter": {**ON, "enabled": False}}))
        self.assertFalse(loaded["enabled"])

    def test_anything_missing_or_unreadable_is_disabled(self):
        bad = (None, "", "{broken", "[]", "{}", json.dumps({"over_budget_counter": None}),
               json.dumps({"over_budget_counter": "on"}), json.dumps({"over_budget_counter": {"enabled": True}}),
               json.dumps({"over_budget_counter": {**ON, "enabled": 1}}),
               json.dumps({"over_budget_counter": {**ON, "enabled": "true"}}),
               json.dumps({"over_budget_counter": {**ON, "max_ask_per_cap": "1.18"}}),
               json.dumps({"over_budget_counter": {**ON, "max_ask_per_cap": 1.0}}),
               json.dumps({"over_budget_counter": {**ON, "max_ask_per_cap": 2.5}}),
               json.dumps({"over_budget_counter": {**ON, "min_surplus_per_list": -0.1}}),
               json.dumps({"over_budget_counter": {**ON, "min_surplus_per_list": 1}}),
               '{"over_budget_counter": {"enabled": true, "max_ask_per_cap": NaN, "min_surplus_per_list": 0.1}}')
        for content in bad:
            with self.subTest(content=content):
                self.assertEqual(self.load(content), bc.DISABLED)

    def test_the_default_path_is_this_folders_config(self):
        self.assertEqual(bc.CONFIG, Path(bc.__file__).resolve().parent / "buyer_support.json")


if __name__ == "__main__":
    unittest.main()
