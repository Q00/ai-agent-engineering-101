"""The read-only investigations count what they claim from small fixtures."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import investigate_market as im


class Fixture(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in ("logs/market-intel", "logs/market-buyer", "logs/buyer", "logs/rank-analysis", ".market/signed-offers"):
            (self.root / folder).mkdir(parents=True)
        (self.root / ".market/catalog.json").write_text(json.dumps({
            "g": {"kind": "goods", "category": "bed", "list_price": 100}, "f": {"kind": "food", "category": "kimchi", "list_price": 50}}), encoding="utf-8")

    def write(self, path, value):
        (self.root / path).write_text(json.dumps(value), encoding="utf-8")


class Ledger(Fixture):
    def test_rows_are_unioned_by_sequence_and_round(self):
        self.write("logs/market-intel/a.json", {"rows": [{"seq": 1, "round": 3, "kind": "settle"}, {"seq": 2, "round": 2}]})
        self.write("logs/market-intel/b.json", {"rows": [{"seq": 1, "round": 3, "kind": "settle"}, {"seq": 3, "round": 3, "kind": "broken"}]})
        self.assertEqual(sorted(r["seq"] for r in im.ledger_rows(self.root)), [1, 3])


class Rejections(Fixture):
    def run_log(self, stamp, shop, state, text="Sorry, none in stock"):
        folder = f"{stamp}-x"
        (self.root / "logs/buyer" / folder).mkdir()
        self.write(f"logs/buyer/{folder}/02.response.txt", {"result": {"task": {"status": {"state": state, "message": {"parts": [{"text": text}]}}}}})
        self.write(f"logs/market-buyer/{stamp}-a.json", {"record": {"outcome": "no_deal", "shop_name": shop, "item": "g", "url_at_time": f"http://{shop}",
                   "buyer_log_dir": "C:\\x\\" + folder}, "selection_evidence": [{"shop": shop, "sku": "g", "support_eligible": True},
                   {"shop": "other", "sku": "g", "support_eligible": True}] if shop == "a" else [{"shop": shop, "sku": "g", "support_eligible": True}]})

    def test_rates_alternatives_and_skip_tradeoff(self):
        self.run_log("20261104T100000000000Z", "a", "TASK_STATE_REJECTED")
        self.run_log("20261104T101000000000Z", "a", "TASK_STATE_REJECTED")
        self.run_log("20261104T101500000000Z", "a", "TASK_STATE_INPUT_REQUIRED", "asking 1.")
        self.run_log("20261104T110000000000Z", "b", "TASK_STATE_REJECTED")
        result = im.open_rejections(self.root, "20261104T000000000000Z", [])
        self.assertEqual((result["negotiations"], result["rejected_at_open"]), (4, 3))
        self.assertEqual(result["wording"], {"no_stock": 3})
        self.assertEqual(result["alternatives_at_selection"], {"with_other_eligible_shop": 2, "with_only_this_shop": 1})
        self.assertEqual(result["skip_after_refusal_same_shop_sku"]["15_min"], {"refusals_avoided": 1, "quotes_lost": 1})
        self.assertEqual(result["by_kind"]["goods"], {"attempts": 4, "refused": 3, "rate": .75})


class ShopHistory(Rejections):
    def test_counts_and_skip_simulation(self):
        for i in range(7):
            self.run_log(f"20261104T1000{i:02d}000000Z", "a", "TASK_STATE_REJECTED")
        self.run_log("20261104T100700000000Z", "a", "TASK_STATE_INPUT_REQUIRED", "asking 1.")
        self.run_log("20261104T100800000000Z", "b", "TASK_STATE_INPUT_REQUIRED", "asking 1.")
        result = im.shop_refusal_history(self.root, "20261104T000000000000Z", {})
        self.assertEqual((result["negotiations"], result["refused"]), (9, 7))
        self.assertEqual(result["worst_shops"][0]["refused"], 7)
        self.assertEqual(result["skip_by_recent_refusal_rate"]["last_5_at_least_0.8"], {"refusals_avoided": 2, "quotes_lost": 1, "paid_lost": 0})


class Quality(Fixture):
    def test_goods_rates_by_grade_and_expected_breaks(self):
        rows = [{"kind": "settle", "data": {"unit": f"u{i}", "item": "g", "grade": "C" if i < 4 else "A"}} for i in range(8)]
        rows += [{"kind": "broken", "data": {"unit": "u0"}}, {"kind": "broken", "data": {"unit": "u1"}},
                 {"kind": "settle", "data": {"unit": "fx", "item": "f", "grade": "C"}}]
        self.write(".market/signed-offers/a.json", {"unit": "own1", "sku": "g", "cost": 50, "price": 90})
        self.write(".market/local-settlements.json", {"records": {"x": {"unit": "own1", "grade": "C"}}})
        self.write("logs/rank-analysis/board-1.json", {"shops": [{"name": im.LOGIN, "deals": 4, "broken": 3}]})
        self.write(".market/seller-quality.json", {})
        result = im.quality(self.root, rows)
        self.assertEqual(result["market_goods_rate"]["grade_C"], {"deals": 4, "broken": 2, "rate": .5})
        self.assertEqual(result["market_goods_rate"]["grade_A"]["broken"], 0)
        self.assertEqual(result["expected_broken_at_market_rates_for_our_grade_mix"]["scaled_to_official_deals"], 2)
        self.assertEqual(result["own_by_grade"]["C"]["deals"], 1)


class Visits(Fixture):
    def test_stock_relation_and_kind_share(self):
        shops = {"shops": [{"name": im.LOGIN, "url": "u0", "stock": {"g": 2}}, {"name": "a", "url": "u1", "stock": {"g": 5, "f": 5}},
                           {"name": "b", "url": "u2", "stock": {"g": 9, "f": 9}}]}
        board = {"shops": [{"name": n, "all": .5, "deals": 1, "visits": v} for n, v in ((im.LOGIN, 10), ("a", 20), ("b", 30))]}
        self.write("s.json", shops)
        self.write("b.json", board)
        rows = [{"kind": "settle", "data": {"shop": "u1", "item": "f", "buyer": "x"}}, {"kind": "settle", "data": {"shop": "u0", "item": "g", "buyer": "x"}}]
        result = im.visits(self.root, rows, self.root / "s.json", self.root / "b.json")
        self.assertEqual(result["rank_correlation_with_visits"]["units"], 1)
        self.assertEqual(result["market_settlements_by_kind"], {"food": 1, "goods": 1})
        self.assertEqual(result["own"]["units"], 2)
        self.assertEqual(result["own_stock_category_share_of_settlements"], .5)


if __name__ == "__main__":
    unittest.main()
