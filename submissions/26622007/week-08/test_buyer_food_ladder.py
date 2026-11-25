import collections
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_food_shelf as fs      # noqa: E402
import oneshot                     # noqa: E402

ON = {"enabled": True, "study": "food-ladder-test", "salt": "food-ladder-test", "start": 0, "end": 4e9}
# Food quote fragments as shops wrote them (logs/market-kit.log, .market/measure/orders.jsonl 11/09 09:4x)
TEXT_C = "사세 직화K버팔로윙 1kg (G2560a8c9), grade C, market certificate included, 133 hours of shelf life left, list price 17900."
TEXT_B = "아삭하고 깊은맛 치즈 (G66ded25b), grade B, 89 h of shelf life left, market certificate included."
TEXT_EQ_C = "G3434758a grade=C cert=market-signed shelf=31.0h ask=9800"           # dragonash
TEXT_CAP_C = "G233e6ea4: Grade C (close to its date), 279.1 h shelf life left."   # ddolcom
TEXT_HELP = "G7fa48eaa, 665 h left | need a shelf life, grade or warranty? say e.g. 'need 48h, grade A/B'"
TEXT_NO_WORD = "G66ded25b: B (normal shelf life), about 89 h of shelf life left"      # bjeon01: no 'grade' word


def switch(md, value):
    with open(os.path.join(md, fl.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-ladder{i:04d}"
        if fl.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "food", "use_within_hours": 30.0}
    raise AssertionError


def decide(md, order, text=TEXT_C, steps=(0.9,), resume=True, probing=False, ask=9800, env=None, code_priced=True):
    return fl.decide(md, order, {"ask": ask, "text": text}, steps, resume, probing=probing, code_priced=code_priced,
                     env=env or {})


class GradeTest(unittest.TestCase):
    def test_real_formats(self):
        self.assertEqual(fl.grade(TEXT_C), "C")
        self.assertEqual(fl.grade(TEXT_B), "B")
        self.assertEqual(fl.grade(TEXT_EQ_C), "C")
        self.assertEqual(fl.grade(TEXT_CAP_C), "C")
        self.assertIsNone(fl.grade(TEXT_HELP))          # a question about grades names none
        self.assertIsNone(fl.grade(TEXT_NO_WORD))       # left unlabeled by design: the ladder without the C rung
        self.assertIsNone(fl.grade("grade A, or grade C if you prefer"))


class SwitchTest(unittest.TestCase):
    def test_off_missing_broken_or_env_keeps_steps_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            self.assertEqual(decide(md, o), ((0.9,), None))           # no switch file
            switch(md, "{not json")
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled=False))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, salt=""))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, ON)
            self.assertEqual(decide(md, o, env={"BUYER_FOOD_LADDER": "0"}), ((0.9,), None))

    def test_the_switch_carries_its_window_and_is_off_outside_it(self):
        # the 1033 switch had no end, so arm B would run past the registry's 23:41 until someone turned it off
        # (goods-ladder review 809 I1, same shape)
        start, end = 1794192089.279038, 1794192089.279038 + 12 * 3600    # 11/09 11:41:29.279 ON, 23:41:29.279 max
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            for bad in ({k: v for k, v in ON.items() if k != "end"}, {k: v for k, v in ON.items() if k != "start"},
                        dict(ON, end="23:41"), dict(ON, start=True), dict(ON, start=end, end=start),
                        dict(ON, end=float("inf")), dict(ON, start=float("nan"))):
                switch(md, bad)
                self.assertEqual(fl.config(md, {}, now=start + 60), None, bad)
            switch(md, dict(ON, start=start, end=end))
            self.assertEqual(fl.config(md, {}, now=start - 1), None)
            self.assertEqual(fl.config(md, {}, now=start)["study"], ON["study"])
            self.assertEqual(fl.config(md, {}, now=end - 1)["study"], ON["study"])
            self.assertEqual(fl.config(md, {}, now=end), None)
            q = {"ask": 9800, "text": TEXT_C}
            self.assertEqual(fl.decide(md, o, q, (0.9,), True, probing=False, code_priced=True, env={}, now=end + 1),
                             ((0.9,), None))
            self.assertEqual(fl.decide(md, o, q, (0.9,), True, probing=False, code_priced=True, env={},
                                       now=end - 1)[0], (0.7, 0.8, 0.9))


class ArmTest(unittest.TestCase):
    def test_arm_is_fixed_by_salt_and_order_id(self):
        self.assertEqual(fl.arm({"salt": "x"}, "o-1"), fl.arm({"salt": "x"}, "o-1"))
        self.assertIsNone(fl.arm({"salt": "x"}, ""))
        self.assertIsNone(fl.arm(None, "o-1"))

    def test_independent_of_food_shelf_arm(self):
        cells = collections.Counter(
            (fl.arm({"salt": "food-ladder-1033"}, f"o-{i:012x}"), fs.arm({"salt": "d99c80df743e2cc9"}, f"o-{i:012x}"))
            for i in range(20000))
        for cell in (("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")):
            self.assertAlmostEqual(cells[cell] / 20000, 0.25, delta=0.02)


class EligibilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = self.tmp.name
        switch(self.md, ON)

    def tearDown(self):
        self.tmp.cleanup()

    def test_treatment_c_grade_ladder(self):
        o = order_in("B")
        steps, rec = decide(self.md, o, TEXT_C)
        self.assertEqual(steps, (0.7, 0.8, 0.9))
        self.assertEqual(rec, {"study": "food-ladder-test", "arm": "B", "grade": "C", "steps": [0.7, 0.8, 0.9]})

    def test_treatment_c_grade_written_with_equals(self):
        self.assertEqual(decide(self.md, order_in("B"), TEXT_EQ_C)[0], (0.7, 0.8, 0.9))

    def test_treatment_other_grade_ladder(self):
        steps, rec = decide(self.md, order_in("B"), TEXT_B)
        self.assertEqual(steps, (0.8, 0.9))
        self.assertEqual(rec["grade"], "B")

    def test_control_keeps_steps_but_is_recorded(self):
        steps, rec = decide(self.md, order_in("A"), TEXT_C)
        self.assertEqual(steps, (0.9,))
        self.assertEqual(rec, {"study": "food-ladder-test", "arm": "A", "grade": "C", "steps": [0.9]})

    def test_ineligible_orders_are_untouched_and_unrecorded(self):
        b = order_in("B")
        cases = [
            dict(order=dict(b, kind="goods")),
            dict(order=b, steps=(0.85, 0.9)),             # pricing.json oneshot_steps set: not this study's baseline
            dict(order=b, steps=None),                    # oneshot off
            dict(order=b, resume=None),                   # no quote talk to resume
            dict(order=b, ask=None),
            dict(order=b, ask=True),
            dict(order=b, probing=True),                  # one-won / opening-discount probes own the opening
            dict(order=dict(b, _one_won_default=True)),
            dict(order=dict(b, _food_quote_yield="B")),
            dict(order=dict(b, _food_shelf={"arm": "B", "would_drop": ["olstjr"]})),
            dict(order=dict(b, _b3={"applied": False})),   # B3 hold rung on: the two studies stay disjoint
            dict(order=b, code_priced=False),              # the opening goes to the harness, not the ladder
        ]
        for c in cases:
            steps = c.get("steps", (0.9,))
            got = decide(self.md, c["order"], steps=steps, resume=c.get("resume", True),
                         probing=c.get("probing", False), ask=c.get("ask", 9800),
                         code_priced=c.get("code_priced", True))
            self.assertEqual(got, (steps, None), c)

    def test_food_shelf_without_would_drop_stays_eligible(self):
        o = dict(order_in("B"), _food_shelf={"arm": "B", "would_drop": []})
        self.assertEqual(decide(self.md, o)[0], (0.7, 0.8, 0.9))
        o = dict(order_in("B"), _food_shelf={"arm": "A", "left": {}})    # pick never reached keep()
        self.assertEqual(decide(self.md, o)[0], (0.7, 0.8, 0.9))


def talk(shop, steps, limit, first_ask):
    """Final price of a code-priced talk with a shop answering by rule, or None when it ends with no deal."""
    history, ask = [], first_ask
    for _ in range(10):
        d = oneshot.code_turn(ask, history, limit, steps)
        assert d["price"] <= limit and (d["action"] != "offer" or d["price"] <= ask), (d, limit, ask)
        if d["action"] != "offer":
            return None
        history += [("shop asked", ask), ("you offered", d["price"])]
        nxt = shop(d["price"], first_ask)
        if nxt is None:
            return d["price"]          # taken
        ask = nxt
    return None


class LadderOutcomeTest(unittest.TestCase):
    """Shop types seen in the 11/08-11/09 food talks (review/W-20261109-1033-AX취약점루프/weakness-02-buyer.md 2.3)."""
    LIMIT, ASK = 7000, 6300

    def test_accepting_shop_sells_lower_in_b(self):
        taker = lambda offer, first: None if offer >= 0.75 * first else round(0.8 * first)
        a = talk(taker, (0.9,), self.LIMIT, self.ASK)
        b = talk(taker, (0.8, 0.9), self.LIMIT, self.ASK)
        self.assertEqual(a, int(0.9 * self.ASK))
        self.assertEqual(b, int(0.8 * self.ASK))

    def test_fixed_price_shop_same_price_in_both(self):
        fixed = lambda offer, first: None if offer >= first else first
        self.assertEqual(talk(fixed, (0.9,), self.LIMIT, self.ASK), self.ASK)
        self.assertEqual(talk(fixed, (0.7, 0.8, 0.9), self.LIMIT, self.ASK), self.ASK)

    def test_midpoint_shop_never_worse_in_b(self):
        mid = lambda offer, first: None if offer >= 0.85 * first else round((offer + first) / 2)
        a = talk(mid, (0.9,), self.LIMIT, self.ASK)
        b = talk(mid, (0.7, 0.8, 0.9), self.LIMIT, self.ASK)
        self.assertIsNotNone(b)
        self.assertLessEqual(b, a)

    def test_offers_stay_within_limit_when_ask_over_limit(self):
        mid = lambda offer, first: None if offer >= 0.95 * first else round((offer + first) / 2)
        b = talk(mid, (0.7, 0.8, 0.9), 5000, self.ASK)
        self.assertTrue(b is None or b <= 5000)


class LearningTest(unittest.TestCase):
    """arm B's talks stay out of the shop learning both arms read (buyer_rank_pg, buyer_shop_profile), as the goods
    ladder's (review 459089c I2): food rows are kind food in rank_pg's cells and have no kind filter in the profile."""
    NOW = 1794196648.4

    def test_laddered(self):
        self.assertTrue(fl.laddered({"food_ladder": {"study": "s", "arm": "B", "grade": None, "steps": [0.8, 0.9]}}))
        self.assertFalse(fl.laddered({"food_ladder": {"arm": "A"}}))
        self.assertFalse(fl.laddered({"food_ladder": None}))
        self.assertFalse(fl.laddered({"goods_ladder": {"arm": "B"}}))
        self.assertFalse(fl.laddered({}))
        self.assertFalse(fl.laddered(None))

    def test_shop_profile_skips_arm_b(self):
        import buyer_shop_profile as sp
        rows = [{"ts": self.NOW - 60, "order": f"f{i}", "shop": "H", "kind": "food", "limit": 1000,
                 "asks": [1080, 1080], "state": "TASK_STATE_CANCELED", "price": None,
                 "food_ladder": {"arm": "B", "grade": None, "steps": [0.8, 0.9]}} for i in range(20)]
        rows += [{"ts": self.NOW - 60, "order": f"d{i}", "shop": "H", "kind": "food", "limit": 1000,
                  "asks": [1000, 1000], "state": "TASK_STATE_COMPLETED", "price": 1000, "passed_to_kit": True}
                 for i in range(10)]
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {})
        for r in rows[:20]:
            r["food_ladder"] = {"arm": "A", "grade": None, "steps": [0.9]}
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {"H": 108})

    def test_rank_pg_skips_arm_b(self):
        import buyer_rank_pg as rp

        def row(i, g=None, price=None):
            # orders.jsonl 11/09 12:57 bjeon01 food row (G3434758a, ask 12546), order replaced
            return {"ts": self.NOW - 600, "shop": "bjeon01", "kind": "food", "order": f"o-{i}", "limit": 13000,
                    "quotes": [{"shop": "bjeon01", "ask": 12546}], "asks": [12546], "price": price,
                    "passed_to_kit": price is not None, "state": "TASK_STATE_COMPLETED" if price else "NO_VALUE",
                    "seconds": {"quotes": 5.0, "end": 15.0}, "food_ladder": g}
        paid = {f"o-{i}": "confirmed" for i in range(20)}
        b = {"study": "s", "arm": "B", "grade": None, "steps": [0.8, 0.9]}
        rows = [row(i, price=11291) for i in range(5)] + [row(10 + i, g=b, price=10036) for i in range(5)]
        st = rp.stats_from_rows(rows, paid, self.NOW)
        self.assertEqual((st["shops"]["bjeon01|food"]["n"], st["counts"]["excluded_rows"]), (5, 5))
        rows = [row(i, price=11291) for i in range(5)] + [row(10 + i, g=dict(b, arm="A"), price=10036) for i in range(5)]
        self.assertEqual(rp.stats_from_rows(rows, paid, self.NOW)["shops"]["bjeon01|food"]["n"], 10)

    def test_recent_asks_skip_arm_b_talk_asks_and_prices(self):
        # independent review 0eb0313 I2: hopeless() reads recent_asks, and arm B's ladder asks and deal prices brought
        # min(seen) under arm A's limit, so whether arm A's next order gave up moved with arm B. Opening quotes stay:
        # they come before the ladder. Rows: orders.jsonl 11/09 13:42 bjeon01 G3434758a (arm B, ask 12546, 10036)
        import time as _time
        import market_buyer as mb
        now = _time.time()
        b = {"study": "s", "arm": "B", "grade": None, "steps": [0.8, 0.9]}
        rows = [{"ts": now - 60, "sku": "G3434758a", "asks": [12546], "price": 10036, "food_ladder": b,
                 "quotes": [{"shop": "bjeon01", "sku": "G3434758a", "ask": 12546, "lost": None}]},
                {"ts": now - 60, "sku": "G3434758a", "asks": [14647, 14354], "price": 14280,
                 "food_ladder": dict(b, arm="A", steps=[0.9]), "quotes": []}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertEqual(sorted(mb.recent_asks(md, now=now)["G3434758a"]), [12546, 14280, 14354, 14647])


class TradeMemoryTest(unittest.TestCase):
    """The goods ladder's review 809 I3 on this study: arm B's deals went into memory/market_memory_r3.json with no arm,
    and rule_choose, gem_names, my_avg_price, overpriced and item_stats read their prices for both arms (prod 11/09
    11:41:29-15:17: 59 arm B deals of 66 arm B trades)."""
    SALT = "food-ladder-test"

    def trade(self, order, price, shop="bjeon01", time_="2026-11-09T14:11:58", asks=(18785, 17000), **kw):
        # a trade as market_buyer.py writes it (prod memory 11/09 14:11:58 bjeon01 Gef4d3036, arm B; order replaced)
        t = {"time": time_, "shop": shop, "item": "Gef4d3036", "budget": 49440, "order": order,
             "category": "신선과일(부패주의)", "list_price": 28900, "result": "deal", "price": price,
             "asks": list(asks), "holds": 0, "problems": [], "warnings": [], "why": None, "signed": True, "paid": True}
        t.update(kw)
        return t

    def oid(self, a, tag=""):
        return order_in(a, salt=self.SALT)["order"] + tag

    def setUp(self):
        self.addCleanup(fl.hide_unmarked, None, [])

    def test_trade_arm(self):
        self.assertEqual(fl.trade_arm({"_food_ladder": {"study": "s", "arm": "B", "grade": "C", "steps": [.7, .8, .9]}}),
                         "B")
        self.assertEqual(fl.trade_arm({"_food_ladder": {"arm": "A"}}), "A")
        self.assertIsNone(fl.trade_arm({"_food_ladder": None}))
        self.assertIsNone(fl.trade_arm({}))
        self.assertIsNone(fl.trade_arm(None))

    def test_market_buyer_writes_the_arm_into_the_trade(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_buyer.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.index('memory["trades"].append({')
        self.assertIn('"food_ladder": buyer_food_ladder.trade_arm(order)', src[i:i + 1000])
        self.assertIn('buyer_food_ladder.hide_unmarked(md, memory["trades"])', src)

    def test_marked_arm_b_deals_leave_price_learning(self):
        import market_buyer as mb
        a, b = self.oid("A"), self.oid("B")
        shops = [{"name": "bjeon01", "url": "u1", "reviews": []}, {"name": "choo-chu", "url": "u2", "reviews": []}]
        # prod 11/09: arm A paid 18489 at choo-chu (11:51:46), arm B's ladder 16906 at bjeon01 (14:11:58)
        trades = [self.trade(a, 18489.0, shop="choo-chu", time_="2026-11-09T11:51:46", asks=(18995, 18489),
                             budget=31390, food_ladder="A"),
                  self.trade(b, 16906.0, food_ladder="B")]
        mem = {"trades": trades}
        fl.hide_unmarked(None, trades)
        # arm B's 16906 at bjeon01 must not make bjeon01 the cheaper shop for arm A's next order
        shop, why = mb.rule_choose(shops, mem, "Gef4d3036", asked=("bjeon01", "choo-chu"))
        self.assertEqual(shop["name"], "choo-chu", why)
        self.assertEqual(mb.item_stats(mb.my_history(mem, "bjeon01"), "Gef4d3036"), ([], None))
        self.assertEqual(mb.item_stats(mb.my_history(mem, "choo-chu"), "Gef4d3036"), ([18489], 18489))
        three = [self.trade(self.oid("A", str(i)), 18489.0, shop="choo-chu", food_ladder="A") for i in range(3)]
        mem = {"trades": three + [self.trade(b, 16906.0, food_ladder="B")]}
        # with arm B's deal read, the average is 18093 and bjeon01's 16906 makes it a gem
        self.assertEqual(mb.my_avg_price(mem, "Gef4d3036"), 18489)
        self.assertEqual(mb.gem_names(mem, "Gef4d3036"), set())
        # the trade itself stays in memory: problems, visits and signatures still count
        self.assertEqual(len(mb.my_history(mem, "bjeon01")), 1)
        # arm A's and unstudied trades keep teaching
        self.assertEqual(len(mb.priced(three + [self.trade("o-x", 1.0, food_ladder=None)])), 4)

    def test_unmarked_trades_since_the_switch_are_hidden_when_they_hash_to_b(self):
        a, b = self.oid("A"), self.oid("B")
        with tempfile.TemporaryDirectory() as md:
            switch(md, dict(ON, study=fl.UNMARKED_STUDY, salt=self.SALT, enabled=False))   # off later: the salt reads
            early = next(o for o in (f"o-early{i}" for i in range(400)) if fl.arm({"salt": self.SALT}, o) == "B")
            trades = [self.trade(b, 16906.0), self.trade(a, 18489.0, shop="choo-chu"),
                      self.trade(early, 900.0, time_="2026-11-09T11:41:28")]     # before 11:41:29: never arm B
            hidden = fl.hide_unmarked(md, trades)
            self.assertIn(b, hidden)
            self.assertNotIn(a, hidden)
            self.assertEqual([t["price"] for t in fl.priced(trades)], [18489.0, 900.0])
            # nothing is written back: the trades are as they were
            self.assertNotIn("food_ladder", trades[0])
            # a marked trade is read by its mark, not the hash
            self.assertEqual(fl.priced([self.trade(b, 1, food_ladder="A")])[0]["price"], 1)

    def test_unmarked_without_the_salt_hides_every_unmarked_order_since_the_switch(self):
        with tempfile.TemporaryDirectory() as md:
            trades = [self.trade(self.oid("A"), 18489.0), self.trade("o-old", 3, time_="2026-11-09T09:00:00"),
                      self.trade(None, 5)]
            fl.hide_unmarked(md, trades)                                   # no switch file
            self.assertEqual([t["price"] for t in fl.priced(trades)], [3, 5])
            switch(md, dict(ON, study="another-study"))                     # someone else's salt says nothing
            fl.hide_unmarked(md, trades)
            self.assertEqual([t["price"] for t in fl.priced(trades)], [3, 5])

    def test_hide_never_raises(self):
        self.assertEqual(fl.hide_unmarked("/nonexistent", [None, 3, {"time": 5}]), frozenset())


if __name__ == "__main__":
    unittest.main()
