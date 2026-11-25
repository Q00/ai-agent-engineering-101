"""restock.urgent_listed: a window serves ~14 lines, so listed SKUs with nothing held or on its way go before refills."""
import time
import unittest

import restock

CATALOG = {"F1": {"kind": "food", "list_price": 20000}, "F2": {"kind": "food", "list_price": 20000},
           "P1": {"kind": "goods", "list_price": 400000}, "H1": {"kind": "goods", "list_price": 3000000},
           "H2": {"kind": "goods", "list_price": 1500000}, "X1": {"kind": "goods", "list_price": 100000}}
CFG = {"high_price_proven_skus": ["P1"], "priority_skus": ["F1", "F2", "H1", "H2"]}


def inv(*held, expired=()):
    now = time.time()
    return {"units": [{"sku": s} for s in held] + [{"sku": s, "expires_at": now - 1} for s in expired]}


class UrgentListedTest(unittest.TestCase):
    def test_food_then_empty_goods_then_refills_each_in_list_order(self):
        got = restock.urgent_listed(CFG, inv("P1", "F1", "H1"), CATALOG)
        self.assertEqual(got, ["F2", "H2", "F1", "P1", "H1"])     # sold-out food, empty goods, then refills

    def test_lines_on_their_way_count_as_held_and_expired_units_do_not(self):
        got = restock.urgent_listed(CFG, inv("P1", expired=("H1",)), CATALOG, [{"sku": "H2", "qty": 1}])
        self.assertEqual(got, ["F1", "F2", "H1", "P1", "H2"])
        expired = {"units": [{"sku": "P1", "grade": "expired"}, {"sku": "F1"}]}
        self.assertEqual(restock.urgent_listed(CFG, expired, CATALOG)[:3], ["F2", "P1", "H1"])

    def test_plan_sends_the_empty_skus_before_the_refills(self):
        model = {"active": True, "skus": {}}
        cfg = dict(restock.DEFAULTS, **CFG, max_lines=2, explore_lines=0, cash_buffer=0, keep_cash=0)
        ranked = [(1.0, "P1", 2), (1.0, "H1", 1), (1.0, "H2", 1)]
        orig = restock.wanted, restock.fill_listed
        restock.wanted = lambda *a, **k: list(ranked)
        restock.fill_listed = lambda r, *a, **k: r
        try:
            out = restock.plan({"balance": 10 ** 9, "units": [{"sku": "P1", "category": "c", "cost": 1}]}, CATALOG, [], cfg, model)
        finally:
            restock.wanted, restock.fill_listed = orig
        self.assertEqual([b["sku"] for b in out["buy"]][:2], ["H1", "H2"])

    def test_proven_first_takes_the_order_it_is_given(self):
        agent, formula = [{"sku": "X1", "qty": 1}], [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}]
        self.assertEqual(restock.proven_first(agent, formula, CFG, order=["H2", "P1"]),
                         [{"sku": "H2", "qty": 1}, {"sku": "P1", "qty": 2}, {"sku": "X1", "qty": 1}])
        self.assertEqual(restock.proven_first(agent, formula, CFG)[:2], [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}])

    def test_batch_keeps_the_order_and_queues_only_unlisted_agent_lines(self):
        keep = lambda lines: lines
        agent = [{"sku": "X1", "qty": 1}, {"sku": "P1", "qty": 1}]
        formula = [{"sku": "P1", "qty": 2}, {"sku": "H2", "qty": 1}]
        send, back = restock.batch(agent, [], formula, keep, dict(CFG, max_lines=2), ["H2", "P1"])
        self.assertEqual((send, back), ([{"sku": "H2", "qty": 1}, {"sku": "P1", "qty": 2}], [{"sku": "X1", "qty": 1}]))

    def test_a_small_food_top_up_goes_after_the_goods_lines(self):
        # 11/07 08:23 window: run 2 spent calls on 3 apples and 1 kimchi (14 and 12 bought a minute earlier) and the window
        # closed on G2607d1f6; 04:4x-08:4x: 16 food lines of 1-3 units, 42 goods lines held back
        keep = lambda lines: lines
        formula = [{"sku": "F1", "qty": 3}, {"sku": "F2", "qty": 12}, {"sku": "H1", "qty": 1}, {"sku": "H2", "qty": 2}]
        small = restock.small_food(CATALOG, inv(*["F1"] * 14))
        send, _ = restock.batch([], formula, formula, keep, dict(CFG, max_lines=3), ["F1", "F2", "H1", "H2"], small)
        self.assertEqual([b["sku"] for b in send], ["F2", "H1", "H2"])     # F1's 3 units wait; a 12-unit food line does not
        send, _ = restock.batch([], formula, formula, keep, dict(CFG, max_lines=4), ["F1", "F2", "H1", "H2"], small)
        self.assertEqual([b["sku"] for b in send], ["F2", "H1", "H2", "F1"])   # room left: still bought, last
        send, _ = restock.batch([], formula, formula, keep, dict(CFG, max_lines=3), ["F1", "F2", "H1", "H2"])
        self.assertEqual([b["sku"] for b in send], ["F1", "F2", "H1"])     # no predicate: the order as before
        # an empty food SKU keeps its place even when the food cap or the cash guard trimmed its line (independent review)
        empty = restock.small_food(CATALOG, inv("H1", expired=["F1"]))
        send, _ = restock.batch([], formula, formula, keep, dict(CFG, max_lines=3), ["F1", "F2", "H1", "H2"], empty)
        self.assertEqual([b["sku"] for b in send], ["F1", "F2", "H1"])
        on_way = restock.small_food(CATALOG, inv(), [{"sku": "F1", "qty": 12}])
        self.assertTrue(on_way({"sku": "F1", "qty": 2}))
        self.assertFalse(on_way({"sku": "F1", "qty": 4}) or on_way({"sku": "P1", "qty": 1}) or on_way({"sku": "F1", "qty": True}))
        # an agent line cut at max_lines goes back to the queue
        agent = [{"sku": "X1", "qty": 1}]
        cat = dict(CATALOG, X1={"kind": "food", "list_price": 20000})
        send, back = restock.batch(agent, [], formula, keep, dict(CFG, max_lines=2), ["F2", "H1"], restock.small_food(cat, inv("X1")))
        self.assertEqual(([b["sku"] for b in send], back), (["F2", "H1"], agent))

    def test_with_the_model_refills_go_by_hours_of_stock_left_lowest_first(self):
        # 11/07 06:23 window: run 2 sent 3 apples and 1 kimchi (held 16, 15) and held back G021aae64 (held 1, 7 sold/h)
        model = {"skus": {"F1": {"rate": 100, "my_share": 0.1}, "P1": {"rate": 40, "my_share": 0.05},
                          "H1": {"rate": 10, "my_share": 0.1}}}
        got = restock.urgent_listed(CFG, inv("P1", "H1", *["F1"] * 15), CATALOG, model=model)
        self.assertEqual(got, ["F2", "H2", "P1", "H1", "F1"])      # cover 0.5 h, 1 h, 1.5 h; empty ones first as before

    def test_hours_left_use_our_recent_sales_when_they_beat_the_model(self):
        # 11/07 07:46 window held back G429bd69e (4 held, model 0.94/h -> 4.3 h, sold 4.3/h over 3 h -> 0.9 h)
        model = {"skus": {"F1": {"rate": 100, "my_share": 0.1}, "P1": {"rate": 40, "my_share": 0.05, "mine_rate": 0.5},
                          "H1": {"rate": 10, "my_share": 0.1, "mine_rate": 4}}}
        got = restock.urgent_listed(CFG, inv("P1", "H1", *["F1"] * 15), CATALOG, model=model)
        self.assertEqual(got, ["F2", "H2", "H1", "P1", "F1"])      # H1 1/4 h, P1 0.5 h (its 0.5/h under the model's 2), F1 1.5 h
        for bad in ("x", float("nan"), True, float("inf"), -1, None):
            model["skus"]["H1"]["mine_rate"] = bad
            self.assertEqual(restock.urgent_listed(CFG, inv("P1", "H1", *["F1"] * 15), CATALOG, model=model),
                             ["F2", "H2", "P1", "H1", "F1"])       # unusable: the model's rate alone, as before

    def test_a_refill_the_model_does_not_know_goes_after_the_known_ones(self):
        model = {"skus": {"H1": {"rate": 10, "my_share": 0.1}, "F1": {"rate": "x", "my_share": 0.1}}}
        got = restock.urgent_listed(CFG, inv("P1", "F1", "H1", "H2", "F2"), CATALOG, model=model)
        self.assertEqual(got, ["H1", "F1", "F2", "P1", "H2"])      # unknown rates after the known, food first as before
        for bad in (0, float("nan"), True, None, 1e308):
            m = {"skus": {"H1": {"rate": bad, "my_share": 1e308 if bad == 1e308 else 0.1}}}
            self.assertEqual(restock.urgent_listed(CFG, inv("P1", "F1", "H1"), CATALOG, model=m), ["F2", "H2", "F1", "P1", "H1"])
        self.assertEqual(restock.urgent_listed(CFG, inv("P1", "F1", "H1"), CATALOG, model={"skus": {}}),
                         restock.urgent_listed(CFG, inv("P1", "F1", "H1"), CATALOG))

    def test_a_malformed_setting_lists_nothing(self):
        self.assertEqual(restock.urgent_listed({"priority_skus": "F1"}, inv(), CATALOG), [])


if __name__ == "__main__":
    unittest.main()
