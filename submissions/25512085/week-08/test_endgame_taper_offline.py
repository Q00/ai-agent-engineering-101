"""POLICY §10: boundary times, table-only changes, no network or live files."""
import copy
import unittest
from unittest.mock import patch

import restock
import store
from test_unified_buying_offline import BuyingFixture, NOW


class EndgameTaperTests(BuyingFixture, unittest.TestCase):
    def prepare(self):
        self.hub = store.default_hub()
        self.inv["units"] = []
        self.catalog = {}
        for sku, count in (("hot", 20), ("popular", 2), ("observe", 1)):
            self.inquiry(self.item(sku), count)

    def run_at(self, seconds_left, ratio=.75):
        with patch.object(restock, "ROUND_END_TS", NOW + seconds_left):
            result = restock.plan_with_hub(
                self.hub, self.path, self.catalog, self.inv, [],
                ratio_observation=(ratio, {"source": "mock public", "default": False, "shops": 5}))
        return {b["sku"]: b["qty"] for b in result["buy"]}, self.diag()

    def test_all_boundaries_phase_ratio_grades_depth(self):
        cases = [
            (96*3600+1, "normal", None, 8, ("hot", "popular", "observe")),
            (96*3600, "taper-1", .50, 6, ("hot", "popular", "observe")),
            (96*3600-1, "taper-1", .50, 6, ("hot", "popular", "observe")),
            (72*3600+1, "taper-1", .50, 6, ("hot", "popular", "observe")),
            (72*3600, "taper-2", .30, 4, ("hot", "popular")),
            (72*3600-1, "taper-2", .30, 4, ("hot", "popular")),
            (48*3600+1, "taper-2", .30, 4, ("hot", "popular")),
            (48*3600, "taper-3", .15, 2, ("hot",)),
            (48*3600-1, "taper-3", .15, 2, ("hot",)),
            (36*3600+1, "taper-3", .15, 2, ("hot",)),
            (36*3600, "stop", 0, 0, ()),
            (36*3600-1, "stop", 0, 0, ()),
        ]
        for seconds, name, cap, depth, grades in cases:
            with self.subTest(seconds_left=seconds):
                self.prepare()
                buys, diag = self.run_at(seconds)
                self.assertEqual(diag["endgame_phase"], name)
                self.assertEqual(diag["ratio_cap"], cap)
                self.assertEqual(diag["hours_left"], seconds / 3600)
                self.assertEqual(diag["R"], .75 if cap is None else cap)
                self.assertEqual(set(buys), set(grades))
                self.assertEqual(buys.get("hot", 0), depth)
                self.assertEqual(buys.get("popular", 0), 2 if "popular" in grades else 0)
                self.assertEqual(buys.get("observe", 0), 1 if "observe" in grades else 0)
                if not grades:
                    self.assertEqual(diag["stop"], "round_end")

    def test_lower_chapter9_ratio_is_preserved(self):
        for hours in (100, 90):
            self.prepare()
            _, diag = self.run_at(hours*3600, ratio=.30)
            self.assertEqual(diag["R"], .30)

    def test_market_clamp_precedes_cap_and_cache_remains_untapered(self):
        self.prepare()
        catalog = {"stock": {"list_price": 4_000_000}}
        shops = [{"name": str(i), "stock": {"stock": 100}} for i in range(3)]
        board = [{"name": str(i), "revenue": i, "profit": 0} for i in range(3)]
        observation = restock.market_ratio(shops, board, catalog)
        self.assertEqual(observation[0], restock.RATIO_MAX)
        with patch.object(restock, "ROUND_END_TS", NOW+40*3600):
            restock.plan_with_hub(self.hub, self.path, self.catalog, self.inv, [],
                                  ratio_observation=observation)
        self.assertEqual(self.diag()["R"], .15)
        self.assertEqual(self.hub["market_ratio_cache"]["ratio"], restock.RATIO_MAX)

    def test_table_only_changes_boundary_cap_grades_and_depth(self):
        schedule = copy.deepcopy(restock.ENDGAME_SCHEDULE)
        schedule[80] = schedule.pop(72)
        schedule[80].update(name="custom", ratio_cap=.20, grades=("hot",), hot_max_depth=1)
        with patch.object(restock, "ENDGAME_SCHEDULE", schedule):
            self.prepare()
            buys, diag = self.run_at(85*3600)
            self.assertEqual(buys, {"hot": 1})
            self.assertEqual(diag["endgame_phase"], "custom")
            self.assertEqual(diag["R"], .20)
            self.prepare()
            _, diag = self.run_at(79*3600)
            self.assertEqual(diag["endgame_phase"], "taper-2")

    def test_hot_depth_subtracts_existing_stock_without_minimum_three(self):
        for held, expected in ((0, 2), (1, 1), (2, 0), (3, 0)):
            self.prepare()
            self.stock("hot", held)
            buys, _ = self.run_at(40*3600)
            self.assertEqual(buys.get("hot", 0), expected)

    def test_zero_deficit_prevents_even_hot_stockout_restore(self):
        for hours in (90, 60, 40):
            self.prepare()
            self.stock(self.item("held"), cost=20_000_000)
            buys, diag = self.run_at(hours*3600)
            self.assertEqual(diag["deficit"], 0)
            self.assertGreater(diag["restore_cost"], 0)
            self.assertEqual(diag["W"], 0)
            self.assertEqual(buys, {})

    def test_endgame_keeps_dead_ban_cash_floor_window_cap_and_reservations(self):
        self.prepare()
        self.hub["no_rebuy"] = {"hot": NOW-1}
        buys, diag = self.run_at(60*3600)
        self.assertNotIn("hot", buys)
        self.assertEqual(diag["excluded"]["no_rebuy"], 1)
        state = self.state()
        cap, reserved = state["window_cap"], state["reserved"]
        self.inv["balance"] *= 10
        buys, _ = self.run_at(60*3600)
        self.assertEqual(buys, {})
        self.assertEqual(state["window_cap"], cap)
        self.assertEqual(state["reserved"], reserved)
        self.prepare()
        self.inv["balance"] = 700_000
        buys, diag = self.run_at(40*3600)
        self.assertEqual(buys, {})
        self.assertEqual(diag["cash_floor"], 700_000)

    def test_build_stop_at_exact_36h_is_offline_and_has_diagnostics(self):
        self.prepare()
        with patch.object(restock, "ROUND_END_TS", NOW+36*3600), \
             patch.object(restock, "load_catalog", return_value=self.catalog), \
             patch.object(restock, "load_inventory", return_value=self.inv), \
             patch.object(restock, "load_hub", return_value=self.hub), \
             patch.object(restock, "fetch_ledger_rows", side_effect=AssertionError("fetch forbidden")):
            self.assertEqual(restock.build_restock_plan(self.path), {"buy": []})
        self.assertEqual(self.diag()["endgame_phase"], "stop")
        self.assertEqual(self.diag()["hours_left"], 36)
        self.assertEqual(self.diag()["ratio_cap"], 0)


if __name__ == "__main__":
    unittest.main()
