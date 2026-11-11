import unittest

import restock


class RestockMemoryTest(unittest.TestCase):
    def test_expired_food_is_waste_not_demand(self):
        previous = {"units": [
            {"unit": "expired", "sku": "F1", "category": "food>kimchi", "kind": "food", "expires_at": 90},
            {"unit": "sold", "sku": "G1", "category": "home>bed", "kind": "goods", "expires_at": None},
        ]}
        memory = restock.update_memory(previous, [], now=100)
        self.assertEqual(memory["category_waste"]["food>kimchi"], 1)
        self.assertNotIn("food>kimchi", memory["category_depletion"])
        self.assertEqual(memory["category_depletion"]["home>bed"], 1)

    def test_depletion_outweighs_unseen_exploration(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "K1": {"category": "food>kimchi", "kind": "food", "list_price": 10_000},
            "B1": {"category": "home>bed", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "food>kimchi"}, {"category": "home>bed"}]
        memory = {"category_depletion": {"home>bed": 3}, "sku_depletion": {"B1": 2}}
        buys = restock.plan(inv, catalog, demand, [], memory)
        self.assertEqual(buys[0], {"sku": "B1", "qty": 2})

    def test_plan_respects_line_and_spend_caps(self):
        inv = {"balance": 100_000, "units": []}
        catalog = {
            f"S{n}": {"category": f"c>{n}", "kind": "goods", "list_price": 100_000}
            for n in range(20)
        }
        demand = [{"category": f"c>{n}"} for n in range(20)]
        buys = restock.plan(inv, catalog, demand, [], {})
        self.assertLessEqual(len(buys), restock.MAX_LINES)
        self.assertEqual(buys, [])  # even one estimated lot exceeds 12% of this balance

    def test_ledger_sales_raise_a_category(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "A1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
            "B1": {"category": "c>b", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "c>a"}, {"category": "c>b"}]
        stats = {"categories": {"c>b": {"n": 20, "median_ratio": 0.9}}, "skus": {"B1": {"n": 20, "median": 90_000}}}
        self.assertEqual("A1", restock.plan(inv, catalog, demand, [], {})[0]["sku"])          # alphabetical without data
        self.assertEqual("B1", restock.plan(inv, catalog, demand, [], {}, stats)[0]["sku"])   # the market says b sells

    def test_market_price_below_expected_cost_skips_the_sku(self):
        inv = {"balance": 5_000_000, "units": []}
        catalog = {
            "S1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
            "S2": {"category": "c>a", "kind": "goods", "list_price": 100_000},
        }
        demand = [{"category": "c>a"}]
        stats = {"skus": {"S1": {"n": 5, "median": 40_000}, "S2": {"n": 3, "median": 80_000}}, "categories": {}}
        report = {}
        buys = restock.plan(inv, catalog, demand, [], {}, stats, report)
        self.assertEqual("S2", buys[0]["sku"])
        self.assertEqual([{"sku": "S1", "market": 40_000, "settles": 5, "expected_cost": 60_000}], report["skipped_below_cost"])
        stats["skus"]["S2"]["median"] = 50_000
        self.assertEqual([], restock.plan(inv, catalog, demand, [], {}, stats))   # nothing in the category sells above cost

    def test_stock_capital_is_capped_and_blind_exploration_stops_early(self):
        catalog = {"A1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
                   "B1": {"category": "c>b", "kind": "goods", "list_price": 100_000}}
        demand = [{"category": "c>a"}, {"category": "c>b"}]
        stats = {"categories": {"c>b": {"n": 5, "median_ratio": 0.9, "revenue": 450_000, "median_amount": 90_000}},
                 "skus": {"B1": {"n": 5, "median": 90_000}}}
        small = {"balance": 4_800_000, "units": [{"category": "c>x", "cost": 100_000}] * 2}            # stock 4% of capital
        self.assertEqual({"A1", "B1"}, {b["sku"] for b in restock.plan(small, catalog, demand, [], {}, stats)})
        mid = {"balance": 4_000_000, "units": [{"category": "c>x", "cost": 100_000}] * 8}              # 17%: evidence only
        report = {}
        self.assertEqual(["B1"], [b["sku"] for b in restock.plan(mid, catalog, demand, [], {}, stats, report)])
        self.assertFalse(report["explore"])
        full = {"balance": 2_000_000, "units": [{"category": "c>x", "cost": 100_000}] * 30}            # 3.0M of 5.0M: at 60%
        self.assertEqual([], restock.plan(full, catalog, demand, [], {}, stats, report))
        self.assertLessEqual(report["stock_room"], 0)

    def test_revenue_picks_the_category_and_the_sku_near_the_price_that_sells(self):
        catalog = {"K1": {"category": "food>kimchi", "kind": "food", "list_price": 20_000},
                   "S-stool": {"category": "home>sofa", "kind": "goods", "list_price": 40_000},
                   "S-sofa": {"category": "home>sofa", "kind": "goods", "list_price": 2_000_000},
                   "S-huge": {"category": "home>sofa", "kind": "goods", "list_price": 9_000_000}}
        demand = [{"category": "food>kimchi", "median_price": 20_000}, {"category": "home>sofa", "median_price": 600_000}]
        stats = {"categories": {"food>kimchi": {"n": 700, "median_ratio": 0.9, "revenue": 15_000_000, "median_amount": 21_000},
                                "home>sofa": {"n": 100, "median_ratio": 0.85, "revenue": 170_000_000, "median_amount": 1_700_000}},
                 "skus": {}}
        inv = {"balance": 10_000_000, "units": []}
        buys = restock.plan(inv, catalog, demand, [], {}, stats)
        self.assertEqual("S-sofa", buys[0]["sku"])          # 100 settles but 11x the revenue; the SKU nearest 2.0M list, not the stool
        self.assertEqual(1, buys[0]["qty"])                 # 1.2M expected cost: one at a time
        self.assertEqual("K1", buys[1]["sku"])
        self.assertNotIn("S-huge", [b["sku"] for b in buys])   # 5.4M expected cost > 15% of the balance

    def test_cheap_leftovers_do_not_block_the_first_real_unit(self):
        catalog = {"stool": {"category": "home>sofa", "kind": "goods", "list_price": 40_000},
                   "sofa": {"category": "home>sofa", "kind": "goods", "list_price": 2_000_000}}
        demand = [{"category": "home>sofa", "median_price": 600_000}]
        stats = {"categories": {"home>sofa": {"n": 100, "median_ratio": 0.85, "revenue": 170_000_000, "median_amount": 1_700_000}}, "skus": {}}
        inv = {"balance": 10_000_000, "units": [{"category": "home>sofa", "sku": "stool", "cost": 20_000, "list_price": 40_000}] * 6}
        self.assertEqual([{"sku": "sofa", "qty": 1}], restock.plan(inv, catalog, demand, [], {}, stats))

    def test_one_category_cannot_take_the_whole_stock_cap(self):
        catalog = {"S1": {"category": "home>sofa", "kind": "goods", "list_price": 1_000_000}}
        demand = [{"category": "home>sofa", "median_price": 1_000_000}]
        inv = {"balance": 5_000_000, "units": [{"category": "home>sofa", "cost": 600_000, "sku": "S1"}] * 3}   # 1.8M of a 4.1M cap: over 35%
        self.assertEqual([], restock.plan(inv, catalog, demand, [], {}, {"categories": {"home>sofa": {"n": 5, "median_ratio": 0.8, "revenue": 4_000_000, "median_amount": 800_000}}}))

    def test_category_pressure_counts_opens_per_deal_over_recent_events(self):
        catalog = {"S": {"category": "home>sofa"}, "K": {"category": "food>kimchi"}}
        events = ([{"at": "2026-11-05 00:10:00", "event": "seller_open", "sku": "S"}] * 6
                  + [{"at": "2026-11-05 00:11:00", "event": "seller_deal", "sku": "S"}] * 2
                  + [{"at": "2026-11-04 10:00:00", "event": "seller_open", "sku": "S"}] * 50      # too old
                  + [{"at": "2026-11-05 00:12:00", "event": "seller_open", "sku": "K"}] * 3)       # under 5 opens
        pressure = restock.category_pressure(events, catalog, "2026-11-04 19:00:00")
        self.assertEqual({"home>sofa": {"opens": 6, "deals": 2, "opens_per_deal": 3.0, "close_rate": 0.333}}, pressure)

    def test_a_category_whose_units_do_not_move_is_not_bought_again(self):
        catalog = {"S1": {"category": "home>sofa", "kind": "goods", "list_price": 1_000_000}}
        demand = [{"category": "home>sofa", "median_price": 1_000_000}]
        stats = {"categories": {"home>sofa": {"n": 50, "median_ratio": 0.8, "revenue": 40_000_000, "median_amount": 800_000}}, "skus": {}}
        inv = {"balance": 10_000_000, "units": [{"category": "home>sofa", "sku": "S1", "cost": 600_000, "list_price": 1_000_000}]}
        slow = {"home>sofa": {"opens": 25, "deals": 4, "close_rate": 0.16}}
        report = {}
        self.assertEqual([], restock.plan(inv, catalog, demand, [], {}, stats, report, slow))
        self.assertEqual(["sofa"], report["skipped_slow"])
        self.assertEqual(["S1"], [b["sku"] for b in restock.plan(inv, catalog, demand, [], {}, stats, {}, {"home>sofa": {"opens": 25, "deals": 15, "close_rate": 0.6}})])

    def test_a_sku_the_whole_market_is_buying_is_left_alone(self):
        catalog = {"S1": {"category": "c>a", "kind": "goods", "list_price": 100_000},
                   "S2": {"category": "c>a", "kind": "goods", "list_price": 100_000}}
        demand = [{"category": "c>a"}]
        stats = {"skus": {}, "categories": {}, "supplied_1h": {"S1": 60, "S2": 5}}   # patch 3.4: S1's cost is climbing
        report = {}
        buys = restock.plan({"balance": 5_000_000, "units": []}, catalog, demand, [], {}, stats, report)
        self.assertEqual("S2", buys[0]["sku"])
        self.assertEqual([{"sku": "S1", "crowded": 60}], report["skipped_below_cost"])

    def test_forum_requires_ledger_reference(self):
        forum = [{"text": "kimchi wanted", "refs": []}, {"text": "kimchi bought", "refs": [10]}]
        self.assertEqual(restock.forum_scores(forum, ["food>kimchi"])["food>kimchi"], 1)


if __name__ == "__main__":
    unittest.main()
