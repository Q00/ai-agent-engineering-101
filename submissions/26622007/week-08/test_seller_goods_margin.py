import json
import tempfile
import unittest
from pathlib import Path

import profit_price
import seller_goods_margin as gm

ON = 1794283200.0          # 11/10 13:00 KST


def unit(**kw):
    # the kit log 11/10 10:25-11:45: a grade C G75f11c19 unit, real cost 46,641, list 122,900, settled at its floor 71,279
    return {"sku": "G75f11c19", "unit": "u-a4279f857a3a", "kind": "goods", "grade": "C", "cost": 46641,
            "list_price": 122900, **kw}


def a_unit(**kw):
    # floor-margin's fixture: a grade A unit of 100만+ furniture at real cost .651 of list
    return unit(sku="G24f5f979", unit="u1", grade="A", cost=1712000, list_price=2630000, **kw)


PRICING = {"price": {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035,
                     "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344},
                     "high_median_up": {"min_list": 300000, "factor": 0.97}, "median_up": 1.0}}


class SellerGoodsMarginTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        d = {"enabled": True, "study": "goods-margin-t", "salt": "s1", "share": 0.08, "treat": 0.5,
             "start_at": ON - 60, "stop_at": ON + 3600, **kw}
        Path(self.md.name, gm.SWITCH).write_text(json.dumps(d))

    def salt_for(self, want, sku="G75f11c19", grade="C", treat=0.5):
        return next(f"s{i}" for i in range(400) if gm.arm({"salt": f"s{i}", "treat": treat}, sku, grade) == want)

    def q(self, u, median=67595, measured=None):
        return profit_price.quote(u, median=median, measured=measured, pricing=PRICING)

    def run_(self, u, q, keep=1, margin=None, a_open=None, floor_margin=None, pricing=PRICING, env=None, now=ON):
        return gm.decide(u, q, reserve=q.get("floor"), ask=q.get("open"), keep=keep, margin_assignment=margin,
                         a_open=a_open, floor_margin=floor_margin, pricing=pricing, md=self.md.name, env=env or {},
                         now=now)

    def test_today_the_reserve_is_break_even_as_in_the_kit_log(self):
        q = self.q(unit())
        self.assertEqual((q["floor"], q["open"], q["b"]), (71279, 71279, 0.344))   # 11:45:45 settled at 71,279

    def test_off_is_todays_price(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.on(enabled=False)
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_GOODS_MARGIN": "0"}), (q["floor"], q["open"], None))

    def test_arm_b_keeps_the_share_after_the_refund_chance(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        reserve, ask, rec = self.run_(u, q)
        # (46,641 + 116.6025 + 0.08 x 122,900) / (1 - 0.344) = 56,589.6025 / 0.656 = 86,264.6 -> 86,265
        self.assertEqual((reserve, ask), (86265, 86265))
        self.assertIs(type(reserve), int)
        # expected profit after the break chance: 86,265 x 0.656 - 46,641 x 1.0025 = 9,833 = 8.0% of list
        self.assertAlmostEqual((reserve * (1 - 0.344) - 46641 * 1.0025) / 122900, 0.08, places=3)
        self.assertEqual((rec["study"], rec["arm"], rec["grade"], rec["share"], rec["treat"]),
                         ("goods-margin-t", "B", "C", 0.08, 0.5))
        self.assertEqual((rec["usual_reserve"], rec["usual_ask"], rec["reserve"], rec["ask"], rec["b"]),
                         (71279, 71279, 86265, 86265, 0.344))

    def test_grade_a_unit(self):
        u = a_unit()
        q = self.q(u, median=1780000, measured=0.03)
        self.on(salt=self.salt_for("B", sku="G24f5f979", grade="A"))
        reserve, ask, rec = self.run_(u, q)
        # (1,712,000 + 4,280 + 210,400) / 0.965 = 1,996,559.6 -> 1,996,560, .759 of list (today 1,778,530, .676)
        self.assertEqual((q["floor"], reserve, ask), (1778530, 1996560, 1996560))
        self.assertEqual(rec["arm"], "B")

    def test_arm_a_keeps_todays_price_and_is_recorded(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("A"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask, rec["arm"], rec["reserve"], rec["ask"]),
                         (q["floor"], q["open"], "A", q["floor"], q["open"]))

    def test_never_lowers_and_never_over_list(self):
        u = unit()
        self.on(salt=self.salt_for("B"))
        high = dict(self.q(u), open=120000)                  # a higher ask stands
        self.assertEqual(self.run_(u, high)[:2], (86265, 120000))
        u2 = unit(cost=81000)                                # today's F 123,786 is over list already: it stands
        q2 = self.q(u2)
        self.assertEqual(self.run_(u2, q2)[0], q2["floor"])
        self.assertGreater(q2["floor"], u2["list_price"])
        u3 = unit(cost=72000)                                # today's F 110,032 under list, the raised one over it
        reserve, ask, rec = self.run_(u3, self.q(u3))
        self.assertEqual((reserve, ask), (122900, 122900))

    def test_other_studies_keep_their_talks(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        self.assertEqual(self.run_(u, q, keep=0.9), same)                                 # markdown talk
        self.assertEqual(self.run_(u, q, margin={"arm": "A"}), same)                      # the margin study's talk
        for rec in ({"arm": "A"}, {"arm": "B"}):
            self.assertEqual(self.run_(u, q, a_open=rec), same)                           # a-open, either arm
            self.assertEqual(self.run_(u, q, floor_margin=rec), same)                     # floor-margin, either arm
        for t in ("trial", "control"):
            self.assertEqual(self.run_(u, dict(q, b_trial=t)), same, t)                   # grade_break_trial unit
        caps = {"price": {**PRICING["price"], "ask_caps": {"G75f11c19": {"C": 90000}}}}
        self.assertEqual(self.run_(u, q, pricing=caps), same)                             # slow stock with a cap
        scarce = {"price": {**PRICING["price"], "scarce_asks": {"G75f11c19": 1}}}
        self.assertEqual(self.run_(u, q, pricing=scarce), same)                           # a scarce-ask SKU, even unused
        trial = {"salt": next(f"t{i}" for i in range(400) if profit_price.trial_arm("G75f11c19", "C", {
            "salt": f"t{i}", "share": 0.5})), "share": 0.5, "factor": 0.9}
        self.assertEqual(self.run_(u, q, pricing={"price": {**PRICING["price"], "high_price_trial": trial}}), same)
        self.assertEqual(gm.decide(u, "not a quote", reserve=q["floor"], ask=q["open"], keep=1, margin_assignment=None,
                                   a_open=None, floor_margin=None, pricing=PRICING, md=self.md.name, env={}, now=ON),
                         (q["floor"], q["open"], None))                                    # a bad q: off, no stderr
        for why in ("ask cap 1 for grade C (was ...)", "scarce ask 1", "trial 0.97 x median 1"):
            self.assertEqual(self.run_(u, dict(q, why=why)), same, why)
        for bad in (unit(kind="food"), unit(list_price=None), unit(list_price=0), unit(cost=None)):
            self.assertEqual(self.run_(bad, {"open": 100, "floor": 90, "why": "", "b": 0.3}), (90, 100, None), bad)

    def test_floor_margin_through_the_real_decide(self):
        # both switches on as seller.py calls them: a floor-margin unit (grade A, either arm) is never priced here,
        # grades B and C are, and once floor-margin stops by itself its A units come in
        import seller_floor_margin as fm
        Path(self.md.name, fm.SWITCH).write_text(json.dumps({"enabled": True, "study": "floor-t", "salt": "fm",
                                                            "share": 0.04, "start_at": ON - 60, "stop_at": ON + 600}))
        self.on(salt="gm", stop_at=ON + 3600)
        for now, a_in in ((ON, False), (ON + 600, True)):
            seen = set()
            for i in range(80):
                for grade in ("A", "B", "C"):
                    u = unit(sku=f"G{i:04d}", grade=grade)
                    q = self.q(u)
                    r1, a1, fmr = fm.decide(u, q, reserve=q["floor"], ask=q["open"], keep=1, margin_assignment=None,
                                            a_open=None, pricing=PRICING, md=self.md.name, env={}, now=now)
                    r2, a2, gmr = gm.decide(u, q, reserve=r1, ask=a1, keep=1, margin_assignment=None, a_open=None,
                                            floor_margin=fmr, pricing=PRICING, md=self.md.name, env={}, now=now)
                    self.assertFalse(fmr and gmr)
                    if fmr:
                        self.assertEqual((r2, a2), (r1, a1))
                    if gmr:
                        seen.add((grade, gmr["arm"]))
            want = {(g, a) for g in ("B", "C") for a in ("A", "B")} | ({("A", "A"), ("A", "B")} if a_in else set())
            self.assertEqual(seen, want, now)

    def test_seller_guards_hold_the_raised_reserve(self):
        import seller
        deal = {"reserve": 86265, "ask": 86265, "open": 86265, "rounds": 1, "kind": "goods",
                "history": [("you asked", 86265), ("buyer offered", 72000)]}
        action, price = seller.price_guards(deal, "accept", 72000, 72000, 86265)
        self.assertEqual((action, price), ("counter", 86265))

    def test_arm_is_per_sku_grade_and_follows_treat(self):
        cfg = {"salt": "x", "treat": 0.5}
        self.assertEqual(gm.arm(cfg, "G1", "A"), gm.arm(cfg, "G1", "A"))
        self.assertEqual({gm.arm(cfg, f"G{i}", "A") for i in range(40)}, {"A", "B"})
        keys = [(f"G{i:04d}", g) for i in range(600) for g in "ABC"]
        for treat in (0.3, 0.5, 0.8):
            b = sum(gm.arm({"salt": "x", "treat": treat}, s, g) == "B" for s, g in keys) / len(keys)
            self.assertAlmostEqual(b, treat, delta=0.04)
        self.assertIsNone(gm.arm(cfg, "G1", None))

    def test_salt_axis_differs_from_floor_margin(self):
        import seller_floor_margin as fm
        skus = [f"G{i:04d}" for i in range(400)]
        same = sum(gm.arm({"salt": "fm", "treat": 0.5}, s, "A") == fm.arm({"salt": "fm"}, s, "A") for s in skus)
        self.assertLess(same, 260)

    def test_bad_settings_are_off(self):
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        for bad in (0.01, 0.2, True, "0.08", float("nan"), None):
            self.on(salt=self.salt_for("B"), share=bad)
            self.assertEqual(self.run_(u, q), same, bad)
        for bad in (0.2, 0.9, True, "0.5", None):
            self.on(salt=self.salt_for("B"), treat=bad)
            self.assertEqual(self.run_(u, q), same, bad)
        for k, v in (("study", ""), ("salt", None), ("start_at", None), ("stop_at", float("inf")), ("stop_at", 10 ** 400),
                     ("stop_at", ON - 60), ("stop_at", ON - 60 + gm.MAX_WINDOW_HOURS * 3600 + 1)):
            self.on(**{"salt": self.salt_for("B"), k: v})
            self.assertEqual(self.run_(u, q), same, (k, v))
        Path(self.md.name, gm.SWITCH).write_text('{"enabled": true, "start_at": NaN')
        self.assertEqual(self.run_(u, q), same)
        Path(self.md.name, gm.SWITCH).write_bytes(b"\xff\xfe")
        self.assertEqual(self.run_(u, q), same)

    def test_window_boundaries(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"), start_at=ON, stop_at=ON + 3600)
        self.assertIsNone(self.run_(u, q, now=ON - 0.001)[2])
        self.assertEqual(self.run_(u, q, now=ON)[2]["arm"], "B")
        self.assertIsNone(self.run_(u, q, now=ON + 3600)[2])

    def test_switch_on_writes_a_finite_window(self):
        d = gm.switch_on(self.md.name, study="goods-margin-t", salt="s", start_at=ON, hours=12, share=0.08, treat=0.5)
        self.assertEqual(d["stop_at"], ON + 12 * 3600)
        self.assertEqual(gm.config(self.md.name, {}, ON)["stop_at"], ON + 12 * 3600)
        self.assertIsNone(gm.config(self.md.name, {}, ON + 12 * 3600))
        for kw in ({"hours": 12.01}, {"hours": 0}, {"share": 0.5}, {"treat": 1.0}, {"salt": ""}):
            with self.assertRaises(ValueError):
                gm.switch_on(self.md.name, **{**dict(study="x", salt="s", start_at=ON, hours=12), **kw})
        self.assertEqual(gm.config(self.md.name, {}, ON)["stop_at"], ON + 12 * 3600)       # the old file stands

    def test_never_raises(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        real = profit_price.floor
        def boom(*a, **k):
            raise RuntimeError("x")
        profit_price.floor = boom
        try:
            self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        finally:
            profit_price.floor = real
        self.assertEqual(gm.decide(unit(), {"open": 5}, reserve=4, ask=5, keep=1, margin_assignment=None, a_open=None,
                                   floor_margin=None, md=self.md.name, env={}, now=ON), (4, 5, None))

    def test_seller_calls_it_after_floor_margin_and_records_it(self):
        src = Path(__file__).with_name("seller.py").read_text(encoding="utf-8")
        f, g, food = (src.index("seller_floor_margin.decide("), src.index("seller_goods_margin.decide("),
                      src.index("seller_food_margin.decide("))
        self.assertLess(f, g)
        self.assertLess(g, food)
        self.assertIn('deal["goods_margin"] = goods_margin', src)
        self.assertIn('**({"goods_margin": deal["goods_margin"]} if deal.get("goods_margin") else {})', src)

    def test_meta_may_not_set_the_switch(self):
        import meta_harness
        self.assertIn(".market/seller_goods_margin.json", meta_harness.SWITCHES)


if __name__ == "__main__":
    unittest.main()
