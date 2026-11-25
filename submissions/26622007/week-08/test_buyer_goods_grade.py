import collections
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_goods_grade as gg     # noqa: E402
import market_buyer as mb          # noqa: E402
import market_model                # noqa: E402

ON = {"enabled": True, "study": "goods-grade-test", "salt": "goods-grade-b-1033", "start": 0, "end": 4e9}
# Goods quote fragments as shops wrote them (.market/measure/orders.jsonl quotes[].text, 11/09 13:xx)
TEXT_B = "QCY T13 APP 무선 블루투스이어폰 ENC 노이즈캔슬링 (G5a3639e8), grade B (certified, 2h warranty). List 23500. Asking 16450."
TEXT_C = "Gx 무선 청소기, grade C (refurbished), 6h warranty. List 100000. Asking 52000."
TEXT_CAP_C = "Gx: Grade C (refurbished), warranty 6 h"                                       # ddolcom
TEXT_A = "Gx 무선 청소기, grade A, 6h warranty. List 100000. Asking 80000."
TEXT_TWO = "Gx grade A at 80000, we also have a cheaper grade C one"
TEXT_NONE = "Gx 무선 청소기, 6h warranty. List 100000. Asking 80000."
TEXT_EQ_C = "Gx grade=C cert=market-signed warranty=6h ask=52000"                           # dragonash: no space form

# 1,000 goods sold: SKU Gx 100 (5 broke), grade A 600/15, B 250/30, C 150/50; Gx grade C 10/4
STATS = {"sold": 1000, "broke": 100, "rate": 0.1,
         "by_sku": {"Gx": {"sold": 100, "broke": 5}},
         "by_grade": {"A": {"sold": 600, "broke": 15}, "B": {"sold": 250, "broke": 30}, "C": {"sold": 150, "broke": 50}},
         "by_shop": {}, "by_sku_grade": {"Gx|C": {"sold": 10, "broke": 4}, "Gx|A": {"sold": 80, "broke": 1}}}
SKU_CHANCE = market_model.break_chance(STATS, "Gx")


def switch(md, value):
    with open(os.path.join(md, gg.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-grade{i:04d}"
        if gg.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "goods"}
    raise AssertionError


def adjust(md, order, text=TEXT_C, stats=STATS, chance=SKU_CHANCE, sw_h=None, env=None, sku="Gx"):
    return gg.adjust(md, order, {"ask": 52000, "text": text}, stats, sku, chance, sw_h, env=env or {})


class Switch(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_off_missing_broken_or_env_zero_keeps_the_sku_chance_unrecorded(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, b), (SKU_CHANCE, None))                   # no switch
        for bad in ("{", "[]", {"enabled": "yes", "study": "s", "salt": "s"}, {"enabled": True, "study": "", "salt": "s"},
                    {"enabled": True, "study": "s"}, {"enabled": False, "study": "s", "salt": "s"}):
            switch(self.md, bad)
            self.assertEqual(adjust(self.md, b), (SKU_CHANCE, None), bad)
        switch(self.md, ON)
        for v in ("0", "false", "off", "No"):
            self.assertEqual(adjust(self.md, b, env={"BUYER_GOODS_GRADE": v}), (SKU_CHANCE, None))
        self.assertNotEqual(adjust(self.md, b)[0], SKU_CHANCE)                     # on: arm B moves it


class Arms(unittest.TestCase):
    def test_the_arm_is_the_first_byte_of_sha256_salt_order(self):
        cfg = {"salt": "goods-grade-b-1033"}
        for i in range(200):
            o = f"ord-{i}"
            want = "B" if hashlib.sha256(f"goods-grade-b-1033:{o}".encode()).digest()[0] % 2 else "A"
            self.assertEqual(gg.arm(cfg, o), want)
        self.assertIsNone(gg.arm(None, "x"))
        self.assertIsNone(gg.arm(cfg, ""))
        self.assertIsNone(gg.arm(cfg, None))

    def test_independent_of_the_food_ladder_and_the_nofit_last_bit(self):
        cells = collections.Counter()
        ladder = {"salt": "food-ladder-1033-35ba16a7d92ca5c5"}
        for i in range(10000):
            o = f"2026-11-09T{i:05d}"
            nofit = "B" if int(hashlib.sha256(o.encode()).hexdigest(), 16) % 2 else "A"
            cells[(gg.arm(ON, o), fl.arm(ladder, o), nofit)] += 1
        self.assertEqual(len(cells), 8)
        for k, n in cells.items():
            self.assertLess(abs(n - 1250), 1250 * 0.1, k)

    def test_grade_reads_the_short_warranty_pattern_one_grade_only(self):
        self.assertEqual(gg.grade(TEXT_B), "B")
        self.assertEqual(gg.grade(TEXT_C), "C")
        self.assertEqual(gg.grade(TEXT_CAP_C), "C")
        self.assertEqual(gg.grade(TEXT_A), "A")
        self.assertIsNone(gg.grade(TEXT_TWO))
        self.assertIsNone(gg.grade(TEXT_NONE))
        self.assertIsNone(gg.grade(TEXT_EQ_C))      # the same reading as market_buyer.short_cap
        self.assertIsNone(gg.grade(None))


class Treatment(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        switch(self.md, ON)
        self.c = market_model.unit_break_chance(STATS, "Gx", "C")

    def test_arm_b_grade_c_takes_the_grade_chance_and_lowers_the_cap(self):
        chance, rec = adjust(self.md, order_in("B"))
        self.assertAlmostEqual(chance, self.c)
        self.assertGreater(self.c, SKU_CHANCE * 3)
        self.assertEqual(rec["arm"], "B")
        self.assertEqual(rec["grade"], "C")
        self.assertEqual(rec["study"], "goods-grade-test")
        self.assertAlmostEqual(rec["sku_chance"], round(SKU_CHANCE, 4))
        self.assertAlmostEqual(rec["grade_chance"], round(self.c, 4))
        self.assertTrue(rec["raised"])
        offer = {"list_price": 100000}
        self.assertLess(mb.saved_cap(offer, chance), mb.saved_cap(offer, SKU_CHANCE))

    def test_arm_a_keeps_the_sku_chance_and_records_what_b_would_use(self):
        chance, rec = adjust(self.md, order_in("A"))
        self.assertEqual(chance, SKU_CHANCE)
        self.assertEqual(rec["arm"], "A")
        self.assertAlmostEqual(rec["grade_chance"], round(self.c, 4))
        self.assertTrue(rec["raised"])          # would raise in B: the same eligible set in both arms

    def test_arm_b_never_lowers_the_chance(self):
        chance, rec = adjust(self.md, order_in("B"), text=TEXT_A)
        self.assertLess(market_model.unit_break_chance(STATS, "Gx", "A"), SKU_CHANCE)
        self.assertEqual(chance, SKU_CHANCE)
        self.assertFalse(rec["raised"])

    def test_untouched_and_unrecorded_outside_the_study(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, dict(b, kind="food")), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, sw_h=2.0), (SKU_CHANCE, None))          # short warranty: its own cap
        self.assertEqual(adjust(self.md, b, sw_h=0), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, text=TEXT_TWO), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, text=TEXT_NONE), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, stats=None), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, b, stats=None, chance=None), (None, None))
        self.assertEqual(adjust(self.md, b, chance=None), (None, None))              # too few sales: no chance at all
        few = dict(STATS, sold=10)
        self.assertEqual(adjust(self.md, b, stats=few), (SKU_CHANCE, None))
        self.assertEqual(adjust(self.md, {"kind": "goods"}), (SKU_CHANCE, None))     # no order id

    def test_never_raises(self):
        b = order_in("B")
        self.assertEqual(adjust(self.md, b, stats={"sold": 1000, "by_grade": "x"}), (SKU_CHANCE, None))
        self.assertEqual(gg.adjust(self.md, b, None, STATS, "Gx", SKU_CHANCE, None, env={}), (SKU_CHANCE, None))
        self.assertEqual(gg.adjust(self.md, None, {}, STATS, "Gx", SKU_CHANCE, None, env={}), (SKU_CHANCE, None))

    def test_the_chance_stays_a_probability(self):
        bad = dict(STATS, by_sku_grade={"Gx|C": {"sold": 1, "broke": 50}})
        chance, rec = adjust(self.md, order_in("B"), stats=bad)
        self.assertEqual((chance, rec), (SKU_CHANCE, None))


class ShopProfile(unittest.TestCase):
    """independent review d85a9e9 I1: arm B's lowered limit must not set a shop's accept band for both arms"""
    NOW = 1_800_000_000.0

    def rows(self, gg_of):
        import buyer_shop_profile as sp  # noqa: F401
        out = []
        for i in range(20):        # a holding shop that ends 8% over my limit when arm B lowered it to 1000 from 1080
            out.append({"ts": self.NOW - 60, "order": f"f{i}", "shop": "H", "limit": 1000, "asks": [1080, 1080],
                        "state": "TASK_STATE_CANCELED", "price": None, "goods_grade": gg_of(i)})
        for i in range(10):
            out.append({"ts": self.NOW - 60, "order": f"d{i}", "shop": "H", "limit": 1000, "asks": [1000, 1000],
                        "state": "TASK_STATE_COMPLETED", "price": 1000, "passed_to_kit": True})
        return out

    def test_arm_b_rows_with_a_lowered_limit_are_left_out(self):
        import buyer_shop_profile as sp
        b = {"study": "s", "arm": "B", "grade": "C", "raised": True, "limit_a": 1080}
        self.assertEqual(sp.profile_from_rows(self.rows(lambda i: b), {}, now=self.NOW), {})
        self.assertEqual(sp.profile_from_rows(self.rows(lambda i: None), {}, now=self.NOW), {"H": 108})
        a = dict(b, arm="A", limit_a=1000)
        self.assertEqual(sp.profile_from_rows(self.rows(lambda i: a), {}, now=self.NOW), {"H": 108})
        same = dict(b, limit_a=1000)                   # B raised the chance but the limit did not move (value_limit)
        self.assertEqual(sp.profile_from_rows(self.rows(lambda i: same), {}, now=self.NOW), {"H": 108})


class RankLearning(unittest.TestCase):
    """independent review 8ca80f5 I1: buyer_rank_pg's shop P and final/first ask, read by both arms, skip arm B's
    lowered talks"""
    NOW = 1794140000.0

    def row(self, i, g=None, price=None):
        r = {"ts": self.NOW - 600, "shop": "a", "kind": "goods", "order": f"o-{i}", "limit": 1000,
             "quotes": [{"shop": "a", "ask": 1200}], "asks": [1200], "price": price, "passed_to_kit": price is not None,
             "state": "TASK_STATE_COMPLETED" if price else "NO_VALUE", "seconds": {"quotes": 5.0, "end": 15.0}}
        if g is not None:
            r["goods_grade"] = g
        return r

    def test_lowered_b_rows_are_not_learned(self):
        import buyer_rank_pg as rp
        b = {"arm": "B", "grade": "C", "limit_a": 1300, "raised": True}
        rows = [self.row(i, price=1100) for i in range(5)] + [self.row(10 + i, g=b) for i in range(5)]
        paid = {f"o-{i}": "confirmed" for i in range(5)}
        st = rp.stats_from_rows(rows, paid, self.NOW)
        self.assertEqual((st["shops"]["a|goods"]["n"], st["shops"]["a|goods"]["k"]), (5, 5))
        self.assertEqual(st["counts"]["excluded_rows"], 5)
        a = dict(b, arm="A", limit_a=1000)
        rows = [self.row(i, price=1100) for i in range(5)] + [self.row(10 + i, g=a) for i in range(5)]
        self.assertEqual(rp.stats_from_rows(rows, paid, self.NOW)["shops"]["a|goods"]["n"], 10)

    def test_lowered(self):
        self.assertTrue(gg.lowered({"limit": 900, "goods_grade": {"arm": "B", "limit_a": 1000}}))
        self.assertFalse(gg.lowered({"limit": 1000, "goods_grade": {"arm": "B", "limit_a": 1000}}))
        self.assertFalse(gg.lowered({"limit": 900, "goods_grade": {"arm": "A", "limit_a": 1000}}))
        self.assertFalse(gg.lowered({"limit": 900, "goods_grade": None}))
        self.assertFalse(gg.lowered({"limit": 900}))
        self.assertFalse(gg.lowered(None))
        self.assertFalse(gg.lowered({"limit": None, "goods_grade": {"arm": "B", "limit_a": 1000}}))


class Wiring(unittest.TestCase):
    def test_market_buyer_calls_it_where_the_limit_is_set(self):
        src = open(mb.__file__, encoding="utf-8").read()
        i = src.index("chance = market_model.break_chance(ledger_breaks(md, catalog), key)")
        j = src.index("limit = min(value_limit(offer, args.budget, chance, buyer_cfg()), saved_cap(offer, chance))", i)
        self.assertIn("buyer_goods_grade.adjust(", src[i:j])
        self.assertIn('"goods_grade": order.get("_goods_grade")', src)
        k = src.index('order["_goods_grade"]["limit_a"] = min(value_limit(offer, args.budget, sku_chance, buyer_cfg()),', j)
        self.assertLess(k - j, 300)      # right after the limit, before the accept band reads it
        self.assertLess(k, src.index("band_base, band_pct = accept_percent(", j))


class Window(unittest.TestCase):
    """The ladder's review 809 I1 holds here too: the 1033 switch had no end, so arm B ran past 01:52 unless turned off."""
    START = 1794199925.458                       # 11/09 13:52:05.458 ON (registry activated-at)
    END = START + 12 * 3600                      # 11/10 01:52:05.458, the registry maximum

    def test_on_only_inside_start_end(self):
        with tempfile.TemporaryDirectory() as md:
            b = order_in("B")
            for bad in ({k: v for k, v in ON.items() if k != "end"}, {k: v for k, v in ON.items() if k != "start"},
                        dict(ON, start="13:52"), dict(ON, start=True), dict(ON, end=False), dict(ON, end=float("inf")),
                        dict(ON, start=float("nan")), dict(ON, start=self.END, end=self.START)):
                switch(md, bad)
                self.assertIsNone(gg.config(md, {}, now=self.START + 60), bad)
            switch(md, dict(ON, start=self.START, end=self.END))
            self.assertIsNone(gg.config(md, {}, now=self.START - 1))
            self.assertEqual(gg.config(md, {}, now=self.END - 1)["study"], ON["study"])
            self.assertIsNone(gg.config(md, {}, now=self.END))
            q = {"ask": 52000, "text": TEXT_C}
            self.assertEqual(gg.adjust(md, b, q, STATS, "Gx", SKU_CHANCE, None, env={}, now=self.END), (SKU_CHANCE, None))
            self.assertGreater(gg.adjust(md, b, q, STATS, "Gx", SKU_CHANCE, None, env={}, now=self.END - 1)[0], SKU_CHANCE)


class TradeMemory(unittest.TestCase):
    """Arm B's lowered limits also reached the buyer memory's trades, whose prices rule_choose, gem_names,
    my_avg_price, overpriced and item_stats read for both arms (main 11/09 14:58, same path as the ladder's 809 I3)."""
    SALT = ON["salt"]

    def setUp(self):
        self.addCleanup(gg.hide_unmarked, None, [])

    def trade(self, order, price, shop="ddolcom", time_="2026-11-09T13:52:10", **kw):
        # a trade as market_buyer.py writes it (prod memory 11/09, shop and order replaced)
        t = {"time": time_, "shop": shop, "item": "Gx", "budget": 120000, "order": order, "category": "청소기",
             "list_price": 100000, "result": "deal", "price": price, "asks": [price], "holds": 0, "problems": [],
             "warnings": [], "why": None, "signed": True, "paid": True}
        t.update(kw)
        return t

    def test_trade_mark(self):
        rec = {"study": "s", "arm": "B", "grade": "C", "limit_a": 70000}
        self.assertEqual(gg.trade_mark({"_goods_grade": rec}, 50000), {"arm": "B", "lowered": True})
        self.assertEqual(gg.trade_mark({"_goods_grade": rec}, 70000), {"arm": "B", "lowered": False})
        self.assertEqual(gg.trade_mark({"_goods_grade": dict(rec, arm="A")}, 70000), {"arm": "A", "lowered": False})
        self.assertIsNone(gg.trade_mark({"_goods_grade": None}, 1))
        self.assertIsNone(gg.trade_mark(None, 1))

    def test_market_buyer_writes_the_mark_and_hides_unmarked(self):
        with open(mb.__file__, encoding="utf-8") as f:
            src = f.read()
        i = src.index('memory["trades"].append({')
        self.assertIn('"goods_grade": buyer_goods_grade.trade_mark(order, limit)', src[i:i + 900])
        self.assertIn('buyer_goods_grade.hide_unmarked(md, memory["trades"])', src)

    def test_lowered_arm_b_deals_leave_price_learning(self):
        a, b = order_in("A")["order"], order_in("B")["order"]
        shops = [{"name": "ddolcom", "url": "u1", "reviews": []}, {"name": "yms979", "url": "u2", "reviews": []}]
        mem = {"trades": [self.trade(a, 80000, shop="yms979", goods_grade={"arm": "A", "lowered": False}),
                          self.trade(b, 52000, goods_grade={"arm": "B", "lowered": True})]}
        gg.hide_unmarked(None, mem["trades"])
        shop, why = mb.rule_choose(shops, mem, "Gx", asked=("ddolcom", "yms979"))
        self.assertEqual(shop["name"], "yms979", why)
        self.assertEqual(mb.item_stats(mb.my_history(mem, "ddolcom"), "Gx"), ([], None))
        # an arm B deal whose limit did not go down is arm A's price: kept
        kept = [self.trade(b, 52000, goods_grade={"arm": "B", "lowered": False})]
        self.assertEqual(mb.item_stats(kept, "Gx"), ([52000], 52000))
        self.assertEqual(len(mb.my_history(mem, "ddolcom")), 1)       # the trade stays for cheats, visits, signatures

    def test_unmarked_since_the_switch_hidden_by_the_study_salt(self):
        a, b = order_in("A")["order"], order_in("B")["order"]
        early = next(o for o in (f"o-early{i}" for i in range(400)) if gg.arm({"salt": self.SALT}, o) == "B")
        with tempfile.TemporaryDirectory() as md:
            switch(md, dict(ON, study=gg.UNMARKED_STUDY, enabled=False))
            trades = [self.trade(b, 52000), self.trade(a, 80000), self.trade(early, 1, time_="2026-11-09T13:52:04")]
            hidden = gg.hide_unmarked(md, trades)
            self.assertEqual((b in hidden, a in hidden, early in hidden), (True, False, False))
            self.assertEqual([t["price"] for t in gg.priced(trades)], [80000, 1])
            self.assertNotIn("goods_grade", trades[0])                  # nothing written back
            self.assertEqual(gg.priced([self.trade(b, 7, goods_grade=None)])[0]["price"], 7)   # marked: by its mark
            trash = os.path.join(md, gg.SWITCH)
            os.remove(trash)
            gg.hide_unmarked(md, trades)                                  # no salt: every unmarked one since 13:52:05
            self.assertEqual([t["price"] for t in gg.priced(trades)], [1])

    def test_both_studies_filter_together(self):
        import buyer_goods_ladder as gl
        self.addCleanup(gl.hide_unmarked, None, [])
        ts = [self.trade("x1", 1, goods_ladder="B", goods_grade=None),
              self.trade("x2", 2, goods_ladder=None, goods_grade={"arm": "B", "lowered": True}),
              self.trade("x3", 3, goods_ladder="A", goods_grade={"arm": "B", "lowered": False})]
        self.assertEqual([t["price"] for t in mb.priced(ts)], [3])

    def test_hide_never_raises(self):
        self.assertEqual(gg.hide_unmarked("/nonexistent", [None, 3, {"time": 5}]), frozenset())


class RecentAsks(unittest.TestCase):
    """main 11/09 (food-ladder 0eb0313 I2 on this study): hopeless() reads recent_asks; an arm B talk whose limit this
    study lowered gives its opening quotes only, its talk asks and deal price came down with the lowered limit"""

    def test_lowered_b_talk_asks_and_price_are_left_out(self):
        # orders.jsonl 11/09 15:38:48 ddolcom Gc2f7811b: arm B, limit 164860 under arm A's 174054, ask 186480, deal
        # at 164860; an arm B talk whose limit did not move (raised False) and an arm A talk are kept
        import time as _time
        now = _time.time()
        b = {"study": "goods-grade-b-1033", "arm": "B", "grade": "B", "sku_chance": 0.0853, "grade_chance": 0.1746,
             "raised": True, "limit_a": 174054}
        rows = [{"ts": now - 60, "order": "o-0a38f86fbd01", "sku": "Gc2f7811b", "shop": "ddolcom", "asks": [186480],
                 "price": 164860.0, "limit": 164860, "goods_ladder": None, "goods_grade": b,
                 "quotes": [{"shop": "ddolcom", "sku": "Gc2f7811b", "ask": 186480, "lost": None}]},
                {"ts": now - 60, "order": "o-same", "sku": "Gc2f7811b", "shop": "ddolcom", "asks": [180000],
                 "price": 170000.0, "limit": 174054, "goods_grade": dict(b, raised=False)},
                {"ts": now - 60, "order": "o-a", "sku": "Gc2f7811b", "shop": "ddolcom", "asks": [181000],
                 "price": 172000.0, "limit": 174054, "goods_grade": dict(b, arm="A")}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertEqual(sorted(mb.recent_asks(md, now=now)["Gc2f7811b"]),
                             [170000, 172000, 180000, 181000, 186480])


if __name__ == "__main__":
    unittest.main()
