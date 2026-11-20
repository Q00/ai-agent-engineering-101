"""The read-only counteroffer replay and repeat-refusal analysis count what they claim."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import analyze_repeat_refusals as ar
import replay_counter as rc

BASE = 1793700000.0


def row(hours_in, *, paid=False, kind="shelf_life", shop="s", sku="k", hours=None, min_hours=None, surplus=0):
    return {"at": BASE + hours_in * 3600, "shop": shop, "sku": sku, "paid": paid, "price": 10, "kind": None if paid else kind,
            "hours": hours, "required": None, "min_hours": min_hours, "surplus": surplus, "filtered": False}


class RepeatRefusals(unittest.TestCase):
    def test_second_refusal_is_avoided_and_a_lost_paid_deal_is_counted(self):
        rows = [row(0, hours=10), row(0.5, hours=10), row(0.8, paid=True, surplus=70), row(5, hours=10)]
        self.assertEqual(ar.simulate(rows, 1), {"refusals_avoided": 1, "paid_deals_lost": 1, "surplus_lost": 70})

    def test_other_pairs_and_old_refusals_are_not_skipped(self):
        rows = [row(0), row(0.5, sku="other", paid=True, surplus=9), row(3, paid=True, surplus=9)]
        self.assertEqual(ar.simulate(rows, 1), {})

    def test_aged_shelf_life_keeps_a_deal_the_old_unit_could_serve(self):
        rows = [row(0, hours=100), row(1, paid=True, surplus=70, min_hours=50), row(2, hours=100, min_hours=200)]
        self.assertEqual(ar.simulate(rows, 3, aged_shelf=True), {"refusals_avoided": 1})
        self.assertEqual(ar.simulate(rows, 3), {"refusals_avoided": 1, "paid_deals_lost": 1, "surplus_lost": 70})

    def test_repeats_count_only_the_same_shop_sku_and_kind(self):
        rows = [row(0), row(1), row(2, kind="grade"), row(3, sku="b"), row(4, kind="other"), row(5, paid=True)]
        result = ar.repeats(rows)
        self.assertEqual((result["pairs"], result["pairs_repeated"], result["refusals_after_the_first_in_a_pair"]), (3, 1, 1))


class CounterReplay(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in ("logs/market-buyer", "logs/buyer", ".market"):
            (self.root / folder).mkdir(parents=True)
        (self.root / ".market/catalog.json").write_text(json.dumps({"k": {"list_price": 200}, "near": {"list_price": 105}}), encoding="utf-8")
        self.count = 0

    def add(self, text, *, sku="k", metadata=None, trail=None, accept=None):
        self.count += 1
        name = f"20261104T{self.count:06d}000000Z"
        directory = self.root / "logs/buyer" / f"{name}-x"
        directory.mkdir()
        message = {"parts": [{"text": text}]}
        if metadata:
            message["metadata"] = metadata
        (directory / "02.response.txt").write_text(json.dumps({"result": {"task": {"status": {
            "state": "TASK_STATE_INPUT_REQUIRED", "message": message}}}}), encoding="utf-8")
        if trail is None:
            trail = [{"reason_code": "model_quota_hour", "parser_reason": "over_budget"}]
        record = {"outcome": "no_deal", "budget": 100, "item": sku, "buyer_log_dir": "C:\\y\\logs\\buyer\\" + directory.name,
                  "decision_trail": trail, "accept_conditions": accept}
        (self.root / "logs/market-buyer" / f"{name}-a.json").write_text(json.dumps({"record": record}), encoding="utf-8")

    def test_targets_exclusions_and_filter_are_separate(self):
        self.add("asking 118.")
        self.add("asking 119.")
        self.add("asking 110.", sku="near")
        self.add("asking 110.", sku="unknown")
        self.add("asking 110.", metadata={"grade": "C"}, accept={"grades": ["A", "B"]})
        self.add("asking 110.", trail=[{"reason_code": "seller_rejected", "parser_reason": "rule_offer"}])
        result = rc.replay(self.root, "20261104T000000000000Z")
        self.assertEqual(result["counter_outcome"], {"rule_counter_offer": 1, "counter_ratio_exceeded": 1,
                                                     "counter_surplus_too_low": 1, "counter_no_list_price": 1})
        self.assertEqual(result["filter_first"], {"quote_grade_unacceptable": 1})
        self.assertEqual(result["tally"]["over_budget_refusals"], 5)
        self.assertNotIn("acceptance", json.dumps(result).replace("estimates no acceptance", ""))


if __name__ == "__main__":
    unittest.main()
