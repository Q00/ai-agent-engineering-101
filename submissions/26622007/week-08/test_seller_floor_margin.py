import json
import os
import tempfile
import unittest
from pathlib import Path

import profit_price
import seller_floor_margin as fm

ON = 1794225600.0          # 11/09 22:00 KST


def unit(**kw):
    # 24h cut 11/09 20:39: an A unit of 100만+ furniture, real cost .651 of list (units_in.jsonl), sold at .677
    return {"sku": "G24f5f979", "unit": "u1", "kind": "goods", "grade": "A", "cost": 1712000, "list_price": 2630000,
            **kw}


PRICING = {"price": {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035,
                     "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344},
                     "high_median_up": {"min_list": 300000, "factor": 0.97}, "median_up": 1.0}}


class SellerFloorMarginTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        d = {"enabled": True, "study": "floor-margin-t", "salt": "s1", "share": 0.05, "start_at": ON - 60,
             "stop_at": ON + 3600, **kw}
        Path(self.md.name, fm.SWITCH).write_text(json.dumps(d))

    def salt_for(self, want, sku="G24f5f979", grade="A"):
        return next(f"s{i}" for i in range(200) if fm.arm({"salt": f"s{i}"}, sku, grade) == want)

    def q(self, u, median=1780000):
        return profit_price.quote(u, median=median, measured=0.03, pricing=PRICING)

    def run_(self, u, q, keep=1, margin=None, a_open=None, pricing=PRICING, env=None, now=ON):
        return fm.decide(u, q, reserve=q.get("floor"), ask=q.get("open"), keep=keep, margin_assignment=margin,
                         a_open=a_open, pricing=pricing, md=self.md.name, env=env or {}, now=now)

    def test_today_the_reserve_is_break_even(self):
        # the weakness: F = (cost x 1.0025 + 1) / (1 - 0.035), 3.9% over cost, and the first ask is F itself
        u = unit()
        q = self.q(u)
        self.assertEqual((q["floor"], q["open"]), (1778530, 1778530))
        self.assertLess(q["floor"] / u["list_price"], 0.68)

    def test_off_is_todays_price(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.on(enabled=False)
        self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_FLOOR_MARGIN": "0"}), (q["floor"], q["open"], None))

    def test_arm_b_raises_reserve_and_ask_by_the_list_share(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        reserve, ask, rec = self.run_(u, q)
        # ceil((1712000 + 0.0025 x 1712000 + 0.05 x 2630000) / (1 - 0.035)) = ceil(1847780 / 0.965)
        self.assertEqual(reserve, 1914798)
        self.assertEqual(ask, 1914798)                       # today's ask 1778530 is under the new reserve
        self.assertIs(type(reserve), int)
        self.assertIs(type(ask), int)
        self.assertAlmostEqual(reserve / u["list_price"], 0.728, places=3)
        self.assertEqual((rec["study"], rec["arm"], rec["share"], rec["usual_reserve"], rec["usual_ask"]),
                         ("floor-margin-t", "B", 0.05, q["floor"], q["open"]))
        self.assertEqual((rec["reserve"], rec["ask"], rec["list"], rec["cost"]), (1914798, 1914798, 2630000, 1712000))

    def test_arm_a_keeps_todays_price_and_is_recorded(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("A"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask, rec["arm"]), (q["floor"], q["open"], "A"))
        self.assertEqual((rec["reserve"], rec["ask"]), (q["floor"], q["open"]))

    def test_never_lowers_and_never_over_list(self):
        u = unit()
        self.on(salt=self.salt_for("B"))
        high = dict(self.q(u), open=2500000)                 # a higher ask stands
        self.assertEqual(self.run_(u, high)[:2], (1914798, 2500000))
        u2 = unit(cost=2600000)                               # cost near list: the raised reserve stops at list
        q2 = self.q(u2)
        reserve, ask, rec = self.run_(u2, q2)
        self.assertEqual(reserve, 2701038)                    # today's F is over list already; it stands, not lowered
        self.assertGreaterEqual(ask, reserve)
        u3 = unit(cost=2450000)                               # F 2,545,209 under list, the raised one 2,681,477 over
        reserve, ask, rec = self.run_(u3, self.q(u3))
        self.assertEqual((reserve, ask), (2630000, 2630000))

    def test_ineligible_talks_untouched(self):
        self.on(salt=self.salt_for("B"))
        for u in (unit(grade="B"), unit(grade="C"), unit(kind="food"), unit(list_price=None), unit(list_price=0)):
            q = {"open": 100, "floor": 90, "why": "", "b": 0.03}
            self.assertEqual(self.run_(u, q), (90, 100, None), u)
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        self.assertEqual(self.run_(u, q, keep=0.9), same)                         # markdown talk
        self.assertEqual(self.run_(u, q, margin={"arm": "A"}), same)              # the margin study's talk
        self.assertEqual(self.run_(u, q, a_open={"arm": "A"}), same)              # a-open owns it, either arm
        self.assertEqual(self.run_(u, q, a_open={"arm": "B"}), same)
        caps = {"price": {**PRICING["price"], "ask_caps": {"G24f5f979": {"A": 9000000}}}}
        self.assertEqual(self.run_(u, q, pricing=caps), same)                     # slow stock with a cap
        self.assertEqual(self.run_(unit(grade="A"), q, pricing={"price": {**PRICING["price"], "ask_caps": {
            "G24f5f979": {"C": 1}}}})[2]["arm"], "B")
        for why in ("ask cap 1800000 for grade A (was ...)", "scarce ask 1900000", "trial 0.97 x median 1"):
            self.assertEqual(self.run_(u, dict(q, why=why)), same, why)

    def test_a_open_talks_stay_a_opens_through_the_real_decide(self):
        # independent review I1: both switches on, a-open's own decide() feeds this one as seller.py does
        import seller_a_open as ao
        Path(self.md.name, ao.SWITCH).write_text(json.dumps({"enabled": True, "study": "a-open-t", "salt": "ao",
                                                            "ratio": 0.76, "start_at": ON - 60, "stop_at": ON + 3600}))
        self.on(salt="fm")
        seen = set()
        for i in range(60):
            for u in (unit(sku=f"G{i:04d}"), unit(sku=f"C{i:04d}", list_price=119000, cost=77500)):
                q = self.q(u, median=0.69 * u["list_price"])
                ask, rec = ao.decide(u, q, keep=1, margin_assignment=None, md=self.md.name, env={}, now=ON)
                r, a, fmr = self.run_(dict(u), dict(q, open=ask), a_open=rec)
                if u["list_price"] >= ao.MIN_LIST:
                    self.assertIsNotNone(rec)
                    self.assertEqual((r, a, fmr), (q["floor"], ask, None))
                    seen.add(("a-open", rec["arm"]))
                else:
                    self.assertIsNone(rec)
                    self.assertIsNotNone(fmr)
                    seen.add(("floor", fmr["arm"]))
        self.assertEqual(seen, {("a-open", "A"), ("a-open", "B"), ("floor", "A"), ("floor", "B")})

    def test_seller_guards_hold_the_raised_reserve(self):
        # independent review M1: an offer between today's F and arm B's reserve is not taken
        import seller
        deal = {"reserve": 1914798, "ask": 1914798, "open": 1914798, "rounds": 1, "kind": "goods",
                "history": [("you asked", 1914798), ("buyer offered", 1800000)]}
        action, price = seller.price_guards(deal, "accept", 1800000, 1800000, 1914798)
        self.assertEqual((action, price), ("counter", 1914798))

    def test_cheap_a_goods_are_in(self):
        u = unit(sku="Gcheap", list_price=119000, cost=77500)    # under a-open's 30만
        q = self.q(u, median=80000)
        self.on(salt=self.salt_for("B", sku="Gcheap"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual(rec["arm"], "B")
        self.assertGreater(reserve, q["floor"])

    def test_arm_is_per_sku_grade(self):
        cfg = {"salt": "x"}
        self.assertEqual(fm.arm(cfg, "G1", "A"), fm.arm(cfg, "G1", "A"))
        self.assertEqual({fm.arm(cfg, f"G{i}", "A") for i in range(40)}, {"A", "B"})

    def test_salt_axis_differs_from_a_open(self):
        import seller_a_open as ao
        skus = [f"G{i:04d}" for i in range(400)]
        same = sum(fm.arm({"salt": "fm"}, s, "A") == ao.arm({"salt": "fm"}, s, "A") for s in skus)
        self.assertLess(same, 260)      # sha256 of a namespaced key: not the a-open split under the same salt

    def test_bad_settings_are_off(self):
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        for bad in (0.01, 0.2, True, "0.05", float("nan"), None):
            self.on(salt=self.salt_for("B"), share=bad)
            self.assertEqual(self.run_(u, q), same, bad)
        for k, v in (("study", ""), ("salt", None), ("start_at", None), ("stop_at", float("inf")),
                     ("stop_at", ON - 60), ("stop_at", ON - 60 + fm.MAX_WINDOW_HOURS * 3600 + 1)):
            self.on(**{"salt": self.salt_for("B"), k: v})
            self.assertEqual(self.run_(u, q), same, (k, v))
        Path(self.md.name, fm.SWITCH).write_text('{"enabled": true, "start_at": NaN')
        self.assertEqual(self.run_(u, q), same)

    def test_window_boundaries(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"), start_at=ON, stop_at=ON + 3600)
        self.assertIsNone(self.run_(u, q, now=ON - 0.001)[2])
        self.assertEqual(self.run_(u, q, now=ON)[2]["arm"], "B")
        self.assertIsNone(self.run_(u, q, now=ON + 3600)[2])

    def test_never_raises(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        real = profit_price.floor
        def boom(*a, **k):
            raise RuntimeError("x")
        profit_price.floor = boom          # independent review M2: the except branch itself
        try:
            self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        finally:
            profit_price.floor = real
        self.assertEqual(fm.decide(unit(), {"open": 5}, reserve=4, ask=5, keep=1, margin_assignment=None, a_open=None,
                                   md=self.md.name, env={}, now=ON), (4, 5, None))
        self.assertEqual(fm.decide(None, {}, reserve=4, ask=5, keep=1, margin_assignment=None, a_open=None,
                                   md=self.md.name, env={}, now=ON), (4, 5, None))

    def test_producer_writes_a_finite_window(self):
        d = fm.switch_on(self.md.name, study="floor-margin-t", salt="s1", start_at=ON, hours=18)
        self.assertEqual((d["start_at"], d["stop_at"], d["share"]), (ON, ON + 18 * 3600, fm.SHARE))
        self.assertIsNotNone(fm.config(self.md.name, env={}, now=ON + 1))
        self.assertIsNone(fm.config(self.md.name, env={}, now=ON + 18 * 3600))
        self.assertEqual(os.stat(os.path.join(self.md.name, fm.SWITCH)).st_mode & 0o777, 0o600)
        for hours in (0, -1, fm.MAX_WINDOW_HOURS + 0.01, float("nan"), True):
            with self.assertRaises(ValueError):
                fm.switch_on(self.md.name, study="floor-margin-t", salt="s1", start_at=ON, hours=hours)
        self.assertIsNotNone(fm.config(self.md.name, env={}, now=ON + 1))

    def test_seller_calls_it_after_a_open_and_records_it(self):
        src = Path(__file__).with_name("seller.py").read_text(encoding="utf-8")
        a, f = src.index("seller_a_open.decide("), src.index("seller_floor_margin.decide(")
        self.assertLess(a, f)
        self.assertLess(f, src.index('deal = self.open[task.id] = {"id": u["sku"]'))
        self.assertIn('**({"floor_margin": deal["floor_margin"]} if deal.get("floor_margin") else {})', src)


if __name__ == "__main__":
    unittest.main()
