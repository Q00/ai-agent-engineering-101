"""Shop b's worst-cost cap is shared by both restock passes (decided 11-09 after b+2's stop at 15:27): when shop b is on, the
ordinary pass plans a 1M+ goods unit only if held plus planned 1M+ stock at worst cost, counting both passes in the run, stays
within b's cap. Only ever stricter; with b off the ordinary pass is exactly today's."""

import unittest
from unittest.mock import patch

import restock_policy as rp
from test_hi_band import LIMITS, RUNTIME, NOW, CHEAP, HI_LOW, catalog, unit, inventory

WORST = 2000000 * 0.75
# H2 passes the market median test (its median is above worst cost plus storage), so the ordinary pass would buy it today.
HI_PASS = {"distinct_buyers": 5, "settles_6h": 9, "distinct_buyers_6h": 5, "median_price": 1900000}


def band(slots=3, cap=12000000, sales=None):
    return {"slots": slots, "min_list_price": 1000000, "sales": {"H": 2} if sales is None else sales, "max_worst_cost_total": cap}


class SharedCap(unittest.TestCase):
    def plan(self, inv, hi_band, limits=LIMITS):
        cat = catalog()
        intelligence = {**{k: CHEAP for k in cat if k.startswith("c")}, "H": HI_LOW, "H2": HI_PASS}
        with patch.object(rp, "capacity_config", return_value=limits):
            buys, explanation = rp.plan(inv, cat, [{"category": "cat"}], {}, 0, intelligence=intelligence, runtime=RUNTIME, now=NOW, hi_band=hi_band)
        return {b["sku"]: b["qty"] for b in buys}, explanation

    def hi_worst(self, inv, buys):
        held = sum(1 for u in inv["units"] if u["sku"] in ("H", "H2"))
        return (held + buys.get("H", 0) + buys.get("H2", 0)) * WORST

    def test_today_the_ordinary_pass_buys_h2(self):
        buys, _ = self.plan(inventory(), None)
        self.assertGreater(buys.get("H2", 0), 0)

    def test_both_passes_in_one_run_stay_within_the_cap(self):
        inv = inventory()
        buys, explanation = self.plan(inv, band(cap=int(2 * WORST)))
        self.assertEqual(buys.get("H"), 2)
        self.assertNotIn("H2", buys)
        self.assertLessEqual(self.hi_worst(inv, buys), 2 * WORST)
        self.assertEqual(explanation["hi_band"]["worst_cost_total"], round(2 * WORST))
        # Ordinary goods under 1M still fill the run.
        self.assertTrue(any(s.startswith("c") for s in buys))

    def test_the_cap_exactly_reached_is_allowed(self):
        inv = inventory()
        buys, explanation = self.plan(inv, band(slots=1, cap=int(2 * WORST)))
        self.assertEqual((buys.get("H"), buys.get("H2")), (1, 1))
        self.assertEqual(self.hi_worst(inv, buys), 2 * WORST)
        self.assertEqual(explanation["hi_band"]["worst_cost_total"], round(2 * WORST))

    def test_held_stock_over_the_cap_blocks_both_passes(self):
        inv = inventory([unit("H", 1500000)] * 3)
        buys, explanation = self.plan(inv, band(cap=int(2 * WORST)))
        self.assertNotIn("H", buys)
        self.assertNotIn("H2", buys)
        self.assertTrue(any(s.startswith("c") for s in buys))

    def test_b_on_with_no_eligible_sku_still_caps_the_ordinary_pass(self):
        inv = inventory([unit("H", 1500000)])
        buys, _ = self.plan(inv, band(cap=int(WORST), sales={}))
        self.assertNotIn("H2", buys)

    def test_b_off_is_exactly_today(self):
        for inv in (inventory(), inventory([unit("H", 1500000)] * 3)):
            with self.subTest(held=len(inv["units"])):
                today, _ = self.plan(inv, None)
                self.assertGreater(today.get("H2", 0), 0)

    def test_only_stricter(self):
        for inv in (inventory(), inventory([unit("H", 1500000)]), inventory([unit("c0")] * 3)):
            for hb in (band(), band(cap=int(WORST)), band(slots=1, cap=int(3 * WORST)), band(sales={})):
                with self.subTest(units=len(inv["units"]), band=hb):
                    capped, _ = self.plan(inv, hb)
                    uncapped, _ = self.plan(inv, None)
                    # The ordinary pass never plans more 1M+ units than with b off.
                    self.assertLessEqual(capped.get("H2", 0), uncapped.get("H2", 0))
                    self.assertLessEqual(self.hi_worst(inv, capped), max(hb["max_worst_cost_total"], sum(1 for u in inv["units"] if u["sku"] in ("H", "H2")) * WORST))


if __name__ == "__main__":
    unittest.main()
