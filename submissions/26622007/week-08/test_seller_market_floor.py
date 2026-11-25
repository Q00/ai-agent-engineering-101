"""seller_market_floor: a goods unit drawn cheap is not sold under what buyers pay for its SKU and grade; a unit whose
break-even floor is already there keeps today's price."""
import json
import math
import tempfile
import unittest
from pathlib import Path

import profit_price
import seller_market_floor as mf

ON = 1794310000.0
PRICING = {"price": {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035,
                     "grade_break": {"A": 0.03, "B": 0.15, "C": 0.344},
                     "high_median_up": {"min_list": 300000, "factor": 0.97}, "median_up": 1.0}}
MEDIAN = 700000     # buyers pay 0.70 of list for this SKU and grade (the last 3 h, briefing.json)


def unit(**kw):
    # a grade A unit of 1M furniture drawn at 0.575 of list: the cohort that sold at 0.633 within the hour
    return {"sku": "G03d0199f", "unit": "u1", "kind": "goods", "grade": "A", "cost": 575000, "list_price": 1000000, **kw}


class SellerMarketFloorTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        d = {"enabled": True, "study": "market-floor-t", "salt": "s1", "share": 1.0, "treat": 0.5,
             "start_at": ON - 60, "stop_at": ON + 3600, **kw}
        Path(self.md.name, mf.SWITCH).write_text(json.dumps(d))

    def salt_for(self, want, sku="G03d0199f", grade="A", treat=0.5):
        return next(f"s{i}" for i in range(400) if mf.arm({"salt": f"s{i}", "treat": treat}, sku, grade) == want)

    def q(self, u, median=MEDIAN):
        return profit_price.quote(u, median=median, measured=None, pricing=PRICING)

    def run_(self, u, q, median=MEDIAN, keep=1, env=None, now=ON, pricing=PRICING, **others):
        kw = {"margin_assignment": None, "a_open": None, "floor_margin": None, "goods_margin": None, **others}
        return mf.decide(u, q, reserve=q.get("floor"), ask=q.get("open"), keep=keep, median=median, pricing=pricing,
                         md=self.md.name, env=env or {}, now=now, **kw)

    def test_today_a_cheap_draw_may_go_down_to_its_break_even(self):
        q = self.q(unit())
        # (575,000 + 1,437.5 + 1) / (1 - 0.035) = 597,345.6 -> 597,346; the first ask is 0.97 x the median
        self.assertEqual((q["floor"], q["open"], q["b"]), (597346, 679000, 0.035))

    def test_off_is_todays_price(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))               # no switch
        self.on(enabled=False, salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_MARKET_FLOOR": "0"}), (q["floor"], q["open"], None))
        for now in (ON - 61, ON + 3600, float("nan"), "x"):                            # before, the boundary, no clock
            self.assertEqual(self.run_(u, q, now=now), (q["floor"], q["open"], None), now)
        for bad in ({"stop_at": None}, {"stop_at": ON + 13 * 3600}, {"start_at": "x"}, {"share": 0.5}, {"share": 1.2},
                    {"share": True}, {"treat": 0.1}, {"treat": 1.5}, {"salt": ""}, {"study": 7}, {"enabled": 1}):
            self.on(**{"salt": self.salt_for("B"), **bad})
            self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None), bad)
        Path(self.md.name, mf.SWITCH).write_text("{broken")
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))

    def test_arm_b_holds_a_cheap_draw_at_the_markets_price(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask), (700000, 700000))            # the median; the ask rises to it from 679,000
        self.assertIs(type(reserve), int)
        self.assertEqual((rec["study"], rec["arm"], rec["grade"], rec["share"], rec["treat"], rec["median"], rec["lifted"]),
                         ("market-floor-t", "B", "A", 1.0, 0.5, 700000, True))
        self.assertEqual((rec["usual_reserve"], rec["usual_ask"], rec["reserve"], rec["ask"]), (597346, 679000, 700000, 700000))
        self.on(salt=self.salt_for("B"), share=0.95)
        self.assertEqual(self.run_(u, q)[:2], (665000, 679000))        # 0.95 x 700,000; the ask stays over it
        self.assertEqual(self.run_(u, q, median=700000.4)[:2], (math.ceil(0.95 * 700000.4), 679000))

    def test_arm_a_keeps_todays_price_and_is_recorded(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("A"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask), (q["floor"], q["open"]))
        self.assertEqual((rec["arm"], rec["reserve"], rec["ask"], rec["lifted"]), ("A", 597346, 679000, False))

    def test_a_dear_draw_keeps_its_break_even_and_nothing_is_ever_lowered(self):
        self.on(salt=self.salt_for("B"))
        dear = unit(cost=725000)                                       # floor 0.753 of list, over the 0.70 median
        q = self.q(dear)
        self.assertGreater(q["floor"], MEDIAN)
        reserve, ask, rec = self.run_(dear, q)
        self.assertEqual((reserve, ask), (q["floor"], q["open"]))
        self.assertEqual((rec["arm"], rec["lifted"]), ("B", False))
        u = unit()
        q = self.q(u)
        got = mf.decide(u, q, reserve=710000, ask=720000, keep=1, median=MEDIAN, margin_assignment=None, a_open=None,
                        floor_margin=None, goods_margin=None, pricing=PRICING, md=self.md.name, env={}, now=ON)
        self.assertEqual(got[:2], (710000, 720000))                    # a reserve another rule already raised stays
        self.assertEqual(self.run_(u, q, median=1200000)[:2], (q["floor"], q["open"]))     # a median over list: not used
        self.on(salt=self.salt_for("B"), share=1.05)
        small = unit(list_price=690000, cost=400000)
        self.assertEqual(self.run_(small, self.q(small), median=680000)[:2], (690000, 690000))   # 714,000: the list price

    def test_only_goods_with_a_usable_median_and_no_other_price_rule(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        for median in (None, 0, -5, float("nan"), float("inf"), "700000", True, 250000):   # none, bad, under 0.3 of list
            self.assertEqual(self.run_(u, q, median=median), same, median)
        self.assertEqual(self.run_(u, q, keep=0.8), same)                              # a markdown talk
        for other in ({"margin_assignment": {"arm": "B"}}, {"a_open": {"arm": "A"}}, {"floor_margin": {"arm": "A"}},
                      {"goods_margin": {"arm": "A"}}, {"goods_margin": {"arm": "B"}}):   # each study keeps its own units
            self.assertEqual(self.run_(u, q, **other), same, other)
        food = unit(kind="food")
        self.assertEqual(self.run_(food, self.q(food)), (self.q(food)["floor"], self.q(food)["open"], None))
        self.assertEqual(self.run_(u, {**q, "b_trial": "trial"}), same)                # a grade_break_trial unit
        self.assertEqual(self.run_(u, {**q, "why": "ask cap 5 for grade A"}), same)
        capped = {"price": {**PRICING["price"], "ask_caps": {"G03d0199f": {"A": 650000}}}}
        self.assertEqual(self.run_(u, q, pricing=capped), same)
        scarce = {"price": {**PRICING["price"], "scarce_asks": {"G03d0199f": 900000}}}
        self.assertEqual(self.run_(u, q, pricing=scarce), same)
        for bad in (unit(list_price=0), unit(cost=None), unit(grade=None), unit(sku=None)):
            self.assertIsNone(self.run_(bad, q)[2], bad)

    def test_arms_are_by_sku_and_grade_on_an_axis_of_their_own(self):
        import seller_goods_margin as gm
        cfg = {"salt": "s1", "treat": 0.5}
        arms = [mf.arm(cfg, f"G{i:08x}", "A") for i in range(600)]
        self.assertEqual(arms, [mf.arm(cfg, f"G{i:08x}", "A") for i in range(600)])
        self.assertTrue(240 < arms.count("B") < 360)
        self.assertNotEqual(arms, [mf.arm(cfg, f"G{i:08x}", "B") for i in range(600)])
        same = sum(a == gm.arm(cfg, f"G{i:08x}", "A") for i, a in enumerate(arms))     # the same salt even: another key
        self.assertTrue(240 < same < 360)
        self.assertEqual({mf.arm({"salt": "s1", "treat": 1.0}, f"G{i}", "C") for i in range(50)}, {"B"})
        self.assertIsNone(mf.arm(None, "G1", "A"))

    def test_switch_on_writes_a_finite_window(self):
        d = mf.switch_on(self.md.name, study="x", salt="y", start_at=ON, hours=12, share=0.97, treat=0.7)
        cfg = mf.config(self.md.name, env={}, now=ON + 1)
        self.assertEqual((cfg["share"], cfg["treat"], cfg["stop_at"]), (0.97, 0.7, ON + 12 * 3600))
        self.assertIsNone(mf.config(self.md.name, env={}, now=d["stop_at"]))
        with self.assertRaises(ValueError):
            mf.switch_on(self.md.name, study="x", salt="y", start_at=ON, hours=13)
        with self.assertRaises(ValueError):
            mf.switch_on(self.md.name, study="x", salt="y", start_at=ON, hours=2, share=1.3)
        self.assertIsNotNone(mf.config(self.md.name, env={}, now=ON + 1))               # the old file stays

    def test_a_broken_setting_never_stops_a_sale(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        real = mf.arm
        mf.arm = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        finally:
            mf.arm = real
        self.assertEqual(mf.decide(None, None, reserve=4, ask=5, keep=1, median=3, md=self.md.name, env={}, now=ON), (4, 5, None))

    def test_seller_calls_it_after_goods_margin_and_records_it(self):
        src = Path(__file__).with_name("seller.py").read_text(encoding="utf-8")
        g, m, food = (src.index("seller_goods_margin.decide("), src.index("seller_market_floor.decide("),
                      src.index("seller_food_margin.decide("))
        self.assertLess(g, m)
        self.assertLess(m, food)
        self.assertIn("goods_margin=goods_margin", src[m:m + 400])
        self.assertIn('deal["market_floor"] = market_floor', src)
        self.assertIn('**({"market_floor": deal["market_floor"]} if deal.get("market_floor") else {})', src)

    def test_meta_may_not_set_the_switch(self):
        import meta_harness
        self.assertIn(".market/seller_market_floor.json", meta_harness.SWITCHES)


if __name__ == "__main__":
    unittest.main()
