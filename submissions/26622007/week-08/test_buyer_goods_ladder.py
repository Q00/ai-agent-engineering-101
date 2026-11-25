import collections
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_ladder as fl     # noqa: E402
import buyer_goods_first_one as gfo  # noqa: E402
import buyer_goods_ladder as gl    # noqa: E402
import oneshot                     # noqa: E402

ON = {"enabled": True, "study": "goods-ladder-test", "salt": "goods-ladder-test", "start": 0, "end": 4e9}
# A goods quote as ddolcom wrote it (orders.jsonl 11/09 13:4x: asks [552960], our offer 497664 = 0.9, taken)
ASK, LIST, LIMIT = 552960, 921600, 626772
TEXT = "G8ec51935 grade A, 72 h warranty, ask 552960"


def switch(md, value):
    with open(os.path.join(md, gl.SWITCH), "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def order_in(a, salt=ON["salt"]):
    for i in range(400):
        o = f"o-gladder{i:04d}"
        if gl.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "goods"}
    raise AssertionError


def decide(md, order, steps=(0.9,), resume=True, probing=False, ask=ASK, env=None, code_priced=True):
    return gl.decide(md, order, {"ask": ask, "text": TEXT}, steps, resume, probing=probing, code_priced=code_priced,
                     env=env or {})


def talk(steps, shop):
    """Plays oneshot.code_turn against a shop rule until an offer is taken or someone walks: (price or None, turns)."""
    history, ask = [], ASK
    for n in range(1, 8):
        d = oneshot.code_turn(ask, history, LIMIT, steps)
        assert d["price"] <= max(LIMIT, 0) and d["price"] <= ask
        if d["action"] == "walk_away":
            return None, n
        history += [("shop asked", ask), ("you offered", d["price"])]
        nxt = shop(d["price"], ask)
        if nxt is None:
            return d["price"], n
        ask = nxt
    return None, n


class SwitchTest(unittest.TestCase):
    def test_off_missing_broken_or_env_keeps_steps_and_records_nothing(self):
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            self.assertEqual(decide(md, o), ((0.9,), None))           # no switch file
            switch(md, "{not json")
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled=False))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, enabled="true"))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, dict(ON, salt=""))
            self.assertEqual(decide(md, o), ((0.9,), None))
            switch(md, ON)
            self.assertEqual(decide(md, o, env={"BUYER_GOODS_LADDER": "0"}), ((0.9,), None))
            self.assertEqual(decide(md, o)[0], (0.8, 0.9))

    def test_the_switch_carries_its_window_and_is_off_outside_it(self):
        # review 809 I1: the 1338 switch had no end, so arm B ran on past the registry's 02:16 until someone turned it off
        start, end = 1794201360.055, 1794201360.055 + 12 * 3600       # 11/09 14:16:00.055 ON, 11/10 02:16:00.055 max
        with tempfile.TemporaryDirectory() as md:
            o = order_in("B")
            for bad in ({k: v for k, v in ON.items() if k != "end"}, {k: v for k, v in ON.items() if k != "start"},
                        dict(ON, end="02:16"), dict(ON, start=True), dict(ON, end=float("inf")),
                        dict(ON, start=end, end=start)):
                switch(md, bad)
                self.assertEqual(gl.config(md, {}, now=start + 60), None, bad)
            switch(md, dict(ON, start=start, end=end))
            self.assertEqual(gl.config(md, {}, now=start - 1), None)
            self.assertEqual(gl.config(md, {}, now=start)["study"], ON["study"])
            self.assertEqual(gl.config(md, {}, now=end - 1)["study"], ON["study"])
            self.assertEqual(gl.config(md, {}, now=end), None)
            self.assertEqual(gl.decide(md, o, {"ask": ASK}, (0.9,), True, probing=False, code_priced=True, env={},
                                       now=end + 1), ((0.9,), None))
            self.assertEqual(gl.decide(md, o, {"ask": ASK}, (0.9,), True, probing=False, code_priced=True, env={},
                                       now=end - 1)[0], (0.8, 0.9))

    def test_food_ladder_switch_does_not_turn_this_on(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, fl.SWITCH), "w") as f:
                json.dump({"enabled": True, "study": "food", "salt": "food"}, f)
            self.assertEqual(decide(md, order_in("B")), ((0.9,), None))


class ArmTest(unittest.TestCase):
    def test_arms_record_and_steps(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            self.assertEqual(decide(md, order_in("A")), ((0.9,), {"study": ON["study"], "arm": "A", "steps": [0.9]}))
            self.assertEqual(decide(md, order_in("B")),
                             ((0.8, 0.9), {"study": ON["study"], "arm": "B", "steps": [0.8, 0.9]}))

    def test_hash_is_fixed_and_independent_of_other_studies(self):
        # fixed vector: the arm of an order id never moves between runs
        self.assertEqual([gl.arm({"salt": "goods-ladder-1338"}, f"o{i}") for i in range(8)],
                         ["B" if hashlib.sha256(f"goods-ladder-1338:o{i}".encode()).digest()[0] % 2 else "A"
                          for i in range(8)])
        grade = lambda o: "B" if hashlib.sha256(f"goods-grade-b-1033:{o}".encode()).digest()[0] % 2 else "A"
        nofit = lambda o: int(hashlib.sha256(o.encode()).hexdigest(), 16) % 2
        food = {"salt": "food-ladder-1033-35ba16a7d92ca5c5"}
        cells = collections.Counter()
        n = 20000
        for i in range(n):
            o = f"ord-{i:06d}"
            a = gl.arm({"salt": "goods-ladder-1338"}, o)
            cells["grade", a, grade(o)] += 1
            cells["nofit", a, nofit(o)] += 1
            cells["food", a, fl.arm(food, o)] += 1
        for k, v in cells.items():
            self.assertAlmostEqual(v / n, .25, delta=.02, msg=k)


class EligibleTest(unittest.TestCase):
    def test_left_alone(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            b = order_in("B")
            for kw in ({"steps": (0.8, 0.9)}, {"steps": None}, {"resume": None}, {"probing": True},
                       {"code_priced": False}, {"ask": None}, {"ask": True}, {"ask": "552960"}):
                self.assertEqual(decide(md, b, **{"steps": (0.9,), **kw}), (kw.get("steps", (0.9,)), None), kw)
            self.assertEqual(decide(md, dict(b, kind="food")), ((0.9,), None))
            self.assertEqual(decide(md, dict(b, _b3={"applied": False})), ((0.9,), None))
            self.assertEqual(decide(md, dict(b, _b3=None))[0], (0.8, 0.9))

    def test_never_raises(self):
        with tempfile.TemporaryDirectory() as md:
            switch(md, ON)
            self.assertEqual(gl.decide(md, None, None, (0.9,), True, probing=False, code_priced=True), ((0.9,), None))
            self.assertEqual(gl.decide(md, {"kind": "goods", "order": 5}, {"ask": 1}, (0.9,), True, probing=False,
                                       code_priced=True), ((0.9,), None))

    def test_code_priced_rule_is_the_goods_opening_rule(self):
        q = {"ask": ASK, "ask_src": "metadata", "text": TEXT}
        self.assertIsNotNone(gfo.first_decision(q, LIMIT, (0.9,)))
        self.assertIsNone(gfo.first_decision(dict(q, text="no warranty, as is"), LIMIT, (0.9,)))
        self.assertIsNone(gfo.first_decision(dict(q, ask_src=None), LIMIT, (0.9,)))


class LazyCodePricedTest(unittest.TestCase):
    """independent review 459089c N1: the goods opening rule is read only with the study on, inside decide's guard"""
    def test_not_called_off_and_raising_is_the_current_talk(self):
        calls = []
        def boom():
            calls.append(1)
            raise TypeError("text is not a str")
        with tempfile.TemporaryDirectory() as md:
            self.assertEqual(decide(md, order_in("B"), code_priced=boom), ((0.9,), None))
            self.assertEqual(calls, [])                     # switch off: never read
            switch(md, ON)
            self.assertEqual(decide(md, order_in("B"), code_priced=boom), ((0.9,), None))
            self.assertEqual(decide(md, order_in("B"), code_priced=lambda: False), ((0.9,), None))
            self.assertEqual(decide(md, order_in("B"), code_priced=lambda: True)[0], (0.8, 0.9))

    def test_text_quote_is_code_priced_like_the_goods_opening(self):
        q = {"ask": ASK, "ask_src": "text", "text": f"G8ec51935 grade A, 72 h warranty, {ASK} won"}
        self.assertEqual(gfo.first_decision(q, LIMIT, (0.9,)) is not None,
                         oneshot.turn(None, q["text"], [], LIMIT, (0.9,), ASK, True) is not None)


class LearningTest(unittest.TestCase):
    """independent review 459089c I2: arm B's talks stay out of the shop learning both arms read"""
    NOW = 1794140000.0

    def test_laddered(self):
        self.assertTrue(gl.laddered({"goods_ladder": {"arm": "B"}}))
        self.assertFalse(gl.laddered({"goods_ladder": {"arm": "A"}}))
        self.assertFalse(gl.laddered({"goods_ladder": None}))
        self.assertFalse(gl.laddered({}))
        self.assertFalse(gl.laddered(None))

    def test_shop_profile_skips_arm_b(self):
        import buyer_shop_profile as sp
        rows = [{"ts": self.NOW - 60, "order": f"f{i}", "shop": "H", "limit": 1000, "asks": [1080, 1080],
                 "state": "TASK_STATE_CANCELED", "price": None, "goods_ladder": {"arm": "B"}} for i in range(20)]
        rows += [{"ts": self.NOW - 60, "order": f"d{i}", "shop": "H", "limit": 1000, "asks": [1000, 1000],
                  "state": "TASK_STATE_COMPLETED", "price": 1000, "passed_to_kit": True} for i in range(10)]
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {})
        for r in rows[:20]:
            r["goods_ladder"] = {"arm": "A"}
        self.assertEqual(sp.profile_from_rows(rows, {}, now=self.NOW), {"H": 108})

    def test_rank_pg_skips_arm_b(self):
        import buyer_rank_pg as rp
        def row(i, g=None, price=None):
            return {"ts": self.NOW - 600, "shop": "a", "kind": "goods", "order": f"o-{i}", "limit": 1000,
                    "quotes": [{"shop": "a", "ask": 1200}], "asks": [1200], "price": price,
                    "passed_to_kit": price is not None, "state": "TASK_STATE_COMPLETED" if price else "NO_VALUE",
                    "seconds": {"quotes": 5.0, "end": 15.0}, "goods_ladder": g}
        paid = {f"o-{i}": "confirmed" for i in range(5)}
        rows = [row(i, price=1100) for i in range(5)] + [row(10 + i, g={"arm": "B"}) for i in range(5)]
        st = rp.stats_from_rows(rows, paid, self.NOW)
        self.assertEqual((st["shops"]["a|goods"]["n"], st["counts"]["excluded_rows"]), (5, 5))
        rows = [row(i, price=1100) for i in range(5)] + [row(10 + i, g={"arm": "A"}) for i in range(5)]
        self.assertEqual(rp.stats_from_rows(rows, paid, self.NOW)["shops"]["a|goods"]["n"], 10)


    def test_recent_asks_skip_arm_b_talk_asks_and_prices(self):
        # main 11/09 (food-ladder 0eb0313 I2 on this study): hopeless() reads recent_asks, and arm B's 0.8 talk asks and
        # deal prices brought min(seen) under arm A's limit, so whether arm A's next order gave up moved with arm B.
        # Opening quotes stay: they come before the ladder. Rows: orders.jsonl 11/09 14:18:39 ddolcom G19a70f63 (arm B,
        # ask 1003680, then 810722 and the deal at 810722) and the same SKU in an arm A talk
        import time as _time
        import market_buyer as mb
        now = _time.time()
        b = {"study": "goods-ladder-1338", "arm": "B", "steps": [0.8, 0.9]}
        rows = [{"ts": now - 60, "order": "o-d8579efbb686", "sku": "G19a70f63", "shop": "ddolcom",
                 "asks": [1003680, 810722], "price": 810722.0, "limit": 1135794, "goods_ladder": b,
                 "quotes": [{"shop": "ddolcom", "sku": "G19a70f63", "ask": 1003680, "lost": None}]},
                {"ts": now - 60, "order": "o-a", "sku": "G19a70f63", "shop": "ddolcom", "asks": [1003680, 990000],
                 "price": 990000.0, "limit": 1135794, "goods_ladder": dict(b, arm="A", steps=[0.9]), "quotes": []}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertEqual(sorted(mb.recent_asks(md, now=now)["G19a70f63"]), [990000, 990000, 1003680, 1003680])

class TradeMemoryTest(unittest.TestCase):
    """review 809 I3: arm B's deals went into memory/market_memory_r3.json with no arm, and rule_choose, gem_names,
    my_avg_price, overpriced and item_stats read their prices for both arms (prod 11/09 14:16-14:4x: 15 arm B deals)."""
    SALT = "goods-ladder-test"

    def trade(self, order, price, shop="ddolcom", time_="2026-11-09T14:16:10", **kw):
        # a trade as market_buyer.py writes it (prod memory 11/09 14:16:10, shop and order replaced)
        t = {"time": time_, "shop": shop, "item": "G8ec51935", "budget": 3469200, "order": order,
             "category": "소파/의자/스툴", "list_price": 2320000, "result": "deal", "price": price,
             "asks": [price, price, price], "holds": 0, "problems": [], "warnings": [], "why": None, "signed": True,
             "paid": True}
        t.update(kw)
        return t

    def oid(self, a):
        return order_in(a, salt=self.SALT)["order"]

    def setUp(self):
        self.addCleanup(gl.hide_unmarked, None, [])

    def test_trade_arm(self):
        self.assertEqual(gl.trade_arm({"_goods_ladder": {"study": "s", "arm": "B", "steps": [0.8, 0.9]}}), "B")
        self.assertEqual(gl.trade_arm({"_goods_ladder": {"arm": "A"}}), "A")
        self.assertIsNone(gl.trade_arm({"_goods_ladder": None}))
        self.assertIsNone(gl.trade_arm({}))
        self.assertIsNone(gl.trade_arm(None))

    def test_market_buyer_writes_the_arm_into_the_trade(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_buyer.py"), encoding="utf-8") as f:
            src = f.read()
        i = src.index('memory["trades"].append({')
        self.assertIn('"goods_ladder": buyer_goods_ladder.trade_arm(order)', src[i:i + 900])
        self.assertIn("buyer_goods_ladder.hide_unmarked(md, memory[\"trades\"])", src)

    def test_marked_arm_b_trades_leave_price_learning(self):
        import market_buyer as mb
        a, b = self.oid("A"), self.oid("B")
        shops = [{"name": "ddolcom", "url": "u1", "reviews": []}, {"name": "yms979", "url": "u2", "reviews": []}]
        trades = [self.trade(a, 2000000, shop="yms979", goods_ladder="A"),
                  self.trade(b, 1547208, shop="ddolcom", goods_ladder="B")]
        mem = {"trades": trades}
        gl.hide_unmarked(None, trades)
        # arm B's 1547208 at ddolcom must not make ddolcom the cheaper shop for arm A's next order
        shop, why = mb.rule_choose(shops, mem, "G8ec51935", asked=("ddolcom", "yms979"))
        self.assertEqual(shop["name"], "yms979", why)
        self.assertEqual(mb.item_stats(mb.my_history(mem, "ddolcom"), "G8ec51935"), ([], None))
        self.assertEqual(mb.item_stats(mb.my_history(mem, "yms979"), "G8ec51935"), ([2000000], 2000000))
        three = [self.trade(self.oid("A") + str(i), 2000000, shop="yms979", goods_ladder="A") for i in range(3)]
        mem = {"trades": three + [self.trade(b, 1000000, goods_ladder="B")]}
        # with arm B's deal read, the average is 1750000 and ddolcom's 1000000 makes it a gem
        self.assertEqual(mb.my_avg_price(mem, "G8ec51935"), 2000000)
        self.assertEqual(mb.gem_names(mem, "G8ec51935"), set())
        # the trade itself stays in memory: problems, visits and signatures still count
        self.assertEqual(len(mb.my_history(mem, "ddolcom")), 1)

    def test_unmarked_trades_since_the_switch_are_hidden_when_they_hash_to_b(self):
        import market_buyer as mb
        a, b = self.oid("A"), self.oid("B")
        with tempfile.TemporaryDirectory() as md:
            switch(md, dict(ON, study=gl.UNMARKED_STUDY, salt=self.SALT, enabled=False))   # off later: the salt still reads
            early = next(o for o in (f"o-early{i}" for i in range(400)) if gl.arm({"salt": self.SALT}, o) == "B")
            trades = [self.trade(b, 1547208), self.trade(a, 2000000, shop="yms979"),
                      self.trade(early, 900000, time_="2026-11-09T14:15:59")]     # before 14:16:00: never arm B
            hidden = gl.hide_unmarked(md, trades)
            self.assertIn(b, hidden)
            self.assertNotIn(a, hidden)
            self.assertEqual([t["price"] for t in gl.priced(trades)], [2000000, 900000])
            # nothing is written back: the trades are as they were
            self.assertNotIn("goods_ladder", trades[0])
            # a marked trade is read by its mark, not the hash
            self.assertEqual(gl.priced([self.trade(b, 1, goods_ladder="A")])[0]["price"], 1)

    def test_unmarked_without_the_salt_hides_every_unmarked_order_since_the_switch(self):
        with tempfile.TemporaryDirectory() as md:
            trades = [self.trade(self.oid("A"), 2000000), self.trade("o-old", 3, time_="2026-11-09T09:00:00"),
                      self.trade(None, 5)]
            gl.hide_unmarked(md, trades)                                   # no switch file
            self.assertEqual([t["price"] for t in gl.priced(trades)], [3, 5])
            switch(md, dict(ON, study="another-study"))                     # someone else's salt says nothing
            gl.hide_unmarked(md, trades)
            self.assertEqual([t["price"] for t in gl.priced(trades)], [3, 5])

    def test_hide_never_raises(self):
        self.assertEqual(gl.hide_unmarked("/nonexistent", [None, 3, {"time": 5}]), frozenset())


class TalkTest(unittest.TestCase):
    def test_taker_fixed_and_midpoint_shops(self):
        takes = lambda off, ask: None                                  # takes any offer (ddolcom, yms979 pattern)
        fixed = lambda off, ask: None if off >= ASK else ASK            # counters with its first ask (thisjonghoon)
        mid = lambda off, ask: None if off >= ask - 1000 else (off + ask) // 2   # meets halfway
        floor_ = lambda off, ask: None if off >= int(.9 * ASK) else int(.9 * ASK)  # takes 0.9, counters 0.9 below
        a, b = talk((0.9,), takes), talk((0.8, 0.9), takes)
        self.assertEqual(a[0], int(.9 * ASK))
        self.assertEqual(b[0], int(.8 * ASK))
        self.assertEqual(talk((0.9,), fixed)[0], None if ASK > LIMIT else ASK)
        self.assertEqual(talk((0.8, 0.9), fixed)[0], talk((0.9,), fixed)[0])
        self.assertLessEqual(talk((0.8, 0.9), mid)[0], talk((0.9,), mid)[0])
        self.assertEqual(talk((0.8, 0.9), floor_)[0], int(.9 * ASK))  # worst case: today's price, one turn later

    def test_accept_band_after_the_ladder(self):
        # review N2/N3: a shop that holds over my limit is taken by the band once both rungs and the raise are spent
        import buyer_accept_band as ab
        top = int(LIMIT * 1.05)
        hold = LIMIT + 1000
        for steps in ((0.9,), (0.8, 0.9)):
            history, ask, d = [], ASK, None
            for _ in range(8):
                d = oneshot.code_turn(ask, history, LIMIT, steps)
                d, _note = ab.turn(d, history, LIMIT, top, steps)
                if d["action"] == "walk_away" or d.get("accept_band"):
                    break
                history += [("shop asked", ask), ("you offered", d["price"])]
                ask = max(hold, d["price"]) if d["price"] < hold else d["price"]
            self.assertTrue(d.get("accept_band"), steps)
            self.assertEqual(d["price"], hold)


class HookTest(unittest.TestCase):
    def test_market_buyer_calls_it_after_food_ladder_and_records_it(self):
        src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "market_buyer.py"), encoding="utf-8").read()
        i, j = src.index("buyer_food_ladder.decide("), src.index("buyer_goods_ladder.decide(")
        self.assertLess(i, j)
        self.assertLess(j, src.index("def price_ladder(meta, text, history):"))
        self.assertIn('"goods_ladder": order.get("_goods_ladder"),', src)


if __name__ == "__main__":
    unittest.main()
