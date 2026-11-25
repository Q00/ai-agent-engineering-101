"""buyer_moss_first: moss-and-oak's whole "Also in stock" quote, first turn, priced by code in arm B (default off)."""
import hashlib
import json
import os
import tempfile
import unittest
import unittest.mock

import buyer_moss_first as mf
import market_buyer
import oneshot

G = ("안내를 요청해 주셔서 반갑습니다, 고객님—항상 신뢰할 수 있는 품질과 정직한 인증으로 모십니다. 사이즈오브체어 프로 사무용 의자 "
     "(G4a74caa4), grade A, 12-hour warranty included (full refund if it breaks), market certificate attached. Also in stock in "
     "this category: G00327e80, G04ee8423. Send your offer as a number, or reply 'accept' to buy now. Asking: 518149. ")
NOW = 1_794_300_000.0


W1 = [NOW - 60, NOW - 60 + 1800]      # m1_write's default window, as an order keeps it


def m1_write(md, mtime=None, **kw):
    """buyer_final_m1.json with a window (30 min from NOW - 60 unless given), its file dated start - 1 unless given."""
    d = {"enabled": True, "start": NOW - 60, "end": NOW - 60 + 1800, **kw}
    p = os.path.join(md, oneshot.FINAL_M1_SWITCH)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    st = d["start"]
    m = mtime if mtime is not None else (st - 1 if isinstance(st, (int, float)) and not isinstance(st, bool)
                                         and 1 <= st < 1e11 else 1)
    os.utime(p, (m, m))


def write(md, mtime=None, **kw):
    """The switch, its file dated before its start (mtime: start - 1 unless given)."""
    d = {"enabled": True, "salt": "moss-1110", "start": NOW - 60, "end": NOW - 60 + 9 * 3600, **kw}
    p = os.path.join(md, mf.SWITCH)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f)
    st = d["start"]
    m = mtime if mtime is not None else (st - 1 if isinstance(st, (int, float)) and not isinstance(st, bool)
                                         and 1 <= st < 1e11 else 1)
    os.utime(p, (m, m))


def arm_of(order, salt="moss-1110"):
    return "AB"[hashlib.sha256(("buyer_moss_first:" + salt + ":" + order).encode()).digest()[0] % 2]


def order_in(arm):
    for i in range(200):
        o = f"o-{i:012x}"
        if arm_of(o) == arm:
            return o
    raise AssertionError


class Switch(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()

    def test_missing_or_off_is_none(self):
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))
        write(self.md, enabled=False)
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))
        with open(os.path.join(self.md, mf.SWITCH), "w") as f:
            f.write("{not json")
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_window_must_be_fixed_9_to_12_hours(self):
        for start, end in ((NOW - 60, NOW + 3600),                  # 1 h: under the 9 h minimum
                           (NOW - 60, NOW - 60 + 13 * 3600),        # over the 12 h maximum
                           (NOW + 60, NOW + 60 + 9 * 3600),         # not started
                           (NOW - 10 * 3600, NOW - 60),             # over
                           (NOW - 60, float("inf")), (True, NOW + 9 * 3600), (NOW - 60, 10 ** 400)):
            write(self.md, start=start, end=end)
            self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW), (start, end))
        write(self.md, start=NOW - 60, end=NOW - 60 + 12 * 3600)
        self.assertIsNotNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_written_after_its_start_is_off(self):
        write(self.md, mtime=NOW - 30)            # start NOW - 60: the file came after the window began
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))
        write(self.md, mtime=NOW - 60)
        self.assertEqual(mf.assign(self.md, {"order": "o-1"}, now=NOW)["written"], NOW - 60)

    def test_order_id_checked(self):
        write(self.md)
        for oid in ("\ud800", "o 1", "o-" + "x" * 95, None, 5):
            self.assertIsNone(mf.assign(self.md, {"order": oid}, now=NOW), repr(oid))

    def test_salt_checked(self):
        for salt in ("", "a b", "x" * 65, 5, None):
            write(self.md, salt=salt)
            self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=NOW))

    def test_every_order_in_the_window_gets_an_arm_by_hash(self):
        write(self.md)
        for o in ("o-1", "o-2", "o-abc"):
            ctx = mf.assign(self.md, {"order": o}, now=NOW)
            self.assertEqual(ctx["arm"], arm_of(o))
            self.assertEqual(ctx["seen"], [])
            self.assertEqual(ctx["applied"], [])
        self.assertIsNone(mf.assign(self.md, {"order": ""}, now=NOW))
        self.assertIsNone(mf.assign(self.md, {"order": "o-1"}, now=float("nan")))

    def test_arms_split_about_half(self):
        n = sum(arm_of(f"o-{i:012x}") == "B" for i in range(4000))
        self.assertLess(abs(n - 2000), 150)

    def test_arm_independent_of_our_ask_hash(self):
        both = sum(arm_of(f"o-{i:012x}", "s") ==
                   "AB"[hashlib.sha256(("buyer_our_ask:s:" + f"o-{i:012x}").encode()).digest()[0] % 2]
                   for i in range(4000))
        self.assertLess(abs(both - 2000), 150)


class Turn(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md)

    def ctx(self, arm):
        return mf.assign(self.md, {"order": order_in(arm)}, now=NOW)

    def run_turn(self, ctx, d=None, meta=None, text=G, history=(), limit=600000, steps=(0.9,), quoted=518149,
                 moss=True, goods=True, now=NOW, shop="moss-and-oak"):
        return mf.turn(d, meta, text, list(history), limit, steps, quoted, moss, goods, ctx, self.md, shop, now=now)

    def events(self):
        with open(os.path.join(self.md, mf.JOURNAL), encoding="utf-8") as f:
            return [json.loads(l) for l in f]

    def test_first_offer_is_090_of_the_ask_whatever_the_ladder_steps(self):
        for steps in ((0.8,), (0.95, 1.0), None, ()):
            ctx = self.ctx("B")
            self.assertEqual(self.run_turn(ctx, steps=steps)["price"], int(0.9 * 518149), steps)

    def test_assignment_and_each_seen_turn_are_journaled(self):
        b, a = self.ctx("B"), self.ctx("A")
        self.run_turn(b)
        self.run_turn(a)
        ev = self.events()
        self.assertEqual([(e["k"], e["order"], e["arm"]) for e in ev],
                         [("assign", b["order"], "B"), ("assign", a["order"], "A"),
                          ("seen", b["order"], "B"), ("seen", a["order"], "A")])
        self.assertEqual({k: ev[0][k] for k in ("salt", "start", "end", "started_at")},
                         {"salt": "moss-1110", "start": b["start"], "end": b["end"], "started_at": NOW})
        self.assertEqual({k: ev[2][k] for k in ("shop", "ask", "offer", "step", "limit", "live", "applied")},
                         {"shop": "moss-and-oak", "ask": 518149, "offer": int(0.9 * 518149), "step": 0.9,
                          "limit": 600000, "live": True, "applied": True})
        self.assertEqual((ev[3]["live"], ev[3]["applied"]), (True, False))

    def test_an_assignment_that_cannot_be_journaled_is_not_treated(self):
        # GPT Pro mwa48k8z I: with the journal gone, arm B still priced the turn, and an order cut off before its
        # orders.jsonl row would leave the ITT list. Now the arm stays, untreated, and the row says journaled false.
        md = tempfile.mkdtemp()
        write(md)
        with open(os.path.join(md, os.path.dirname(mf.JOURNAL)), "w") as f:     # a file where its folder goes
            f.write("x")
        ctx = mf.assign(md, {"order": order_in("B")}, now=NOW)
        self.assertEqual((ctx["arm"], ctx["journaled"]), ("B", False))
        self.assertIsNone(mf.turn(None, None, G, [], 600000, (0.9,), 518149, True, True, ctx, md, "moss-and-oak", now=NOW))
        self.assertEqual((len(ctx["seen"]), ctx["applied"]), (1, []))
        self.assertFalse(mf.live(md, ctx, now=NOW))
        self.assertIs(mf.measurement(ctx)["journaled"], False)

    def test_a_seen_turn_that_cannot_be_journaled_is_not_treated(self):
        ctx = self.ctx("B")
        os.chmod(os.path.join(self.md, mf.JOURNAL), 0o400)          # the assign line is in; the seen line cannot be
        try:
            self.assertIsNone(self.run_turn(ctx))
            self.assertEqual(ctx["applied"], [])
            self.assertIs(ctx["seen"][-1]["journaled"], False)        # the row tells it from a not-live turn (review M2)
        finally:
            os.chmod(os.path.join(self.md, mf.JOURNAL), 0o600)
        self.assertEqual(self.run_turn(ctx)["action"], "offer")      # journal back: treated

    def test_off_or_not_seen_journals_nothing(self):
        md = tempfile.mkdtemp()
        self.assertIsNone(mf.assign(md, {"order": "o-1"}, now=NOW))
        self.run_turn(self.ctx("B"), shop="other-shop")
        self.assertFalse(os.path.exists(os.path.join(md, mf.JOURNAL)))
        self.assertEqual([e["k"] for e in self.events()], ["assign"])

    def test_arm_b_prices_the_first_turn_at_the_step_share(self):
        ctx = self.ctx("B")
        d = self.run_turn(ctx)
        self.assertEqual(d, {"shop_price": 518149, "action": "offer", "price": int(0.9 * 518149)})
        self.assertEqual(ctx["applied"], [{"shop": "moss-and-oak", "ask": 518149, "offer": int(0.9 * 518149)}])
        self.assertEqual(ctx["seen"], ctx["applied"])

    def test_never_over_my_limit(self):
        ctx = self.ctx("B")
        self.assertEqual(self.run_turn(ctx, limit=400000)["price"], 400000)

    def test_arm_a_keeps_the_harness_and_notes_what_b_would_do(self):
        ctx = self.ctx("A")
        self.assertIsNone(self.run_turn(ctx))
        self.assertEqual(ctx["applied"], [])
        self.assertEqual(ctx["seen"], [{"shop": "moss-and-oak", "ask": 518149, "offer": int(0.9 * 518149)}])

    def test_no_context_is_legacy(self):
        self.assertIsNone(self.run_turn(None))

    def test_only_the_first_turn_of_a_moss_quote_with_no_code_answer(self):
        for kw in ({"d": {"shop_price": 1, "action": "offer", "price": 1}},     # code already priced it: kept
                   {"meta": 518149},                                            # a metadata ask: oneshot's
                   {"history": [("shop asked", 518149), ("you offered", 466334)]},
                   {"moss": False},                                             # not moss's whole quote
                   {"quoted": None}, {"quoted": True}, {"quoted": 0},
                   {"shop": "other-shop"},                                    # the same quote from another shop
                   {"text": G.replace("12-hour warranty included (full refund if it breaks)", "no warranty")}):
            ctx = self.ctx("B")
            d = self.run_turn(ctx, **kw)
            self.assertEqual(d, kw.get("d"), kw)
            self.assertEqual(ctx["applied"], [], kw)

    def test_food_without_warranty_words_is_priced(self):
        ctx = self.ctx("B")
        self.assertEqual(self.run_turn(ctx, goods=False, text=G.replace("12-hour warranty included (full refund if it breaks)",
                                                                       "no warranty"))["action"], "offer")

    def test_switch_turned_off_mid_order_stops_arm_b(self):
        ctx = self.ctx("B")
        write(self.md, enabled=False)
        self.assertIsNone(self.run_turn(ctx))
        self.assertEqual(ctx["applied"], [])
        self.assertEqual(len(ctx["seen"]), 1)

    def test_switch_changed_or_window_over_stops_arm_b(self):
        ctx = self.ctx("B")
        write(self.md, salt="other")
        self.assertIsNone(self.run_turn(ctx))
        write(self.md)
        self.assertIsNone(self.run_turn(ctx, now=NOW + 10 * 3600))
        self.assertEqual(ctx["applied"], [])

    def test_measurement(self):
        self.assertIsNone(mf.measurement(None))
        ctx = self.ctx("B")
        self.run_turn(ctx)
        m = mf.measurement(ctx)
        self.assertEqual((m["arm"], m["salt"], m["start"], m["end"], m["written"]),
                         ("B", "moss-1110", ctx["start"], ctx["end"], ctx["start"] - 1))
        self.assertEqual(len(m["applied"]), 1)
        self.assertIs(m["journaled"], True)
        json.dumps(m)


class LeftOutOfLearning(unittest.TestCase):
    """An arm B talk this study priced is left out of what both arms learn (independent review 5445a75 I1)."""
    B = {"moss_first": {"arm": "B", "applied": [{"shop": "moss-and-oak", "ask": 100, "offer": 90}]}}

    def rows(self):
        return [dict(self.B), {"moss_first": {"arm": "B", "applied": []}}, {"moss_first": {"arm": "A", "applied": []}},
                {"moss_first": None}, {}]

    def test_applied_b(self):
        self.assertEqual([mf.applied_b(r) for r in self.rows()], [True, False, False, False, False])
        self.assertFalse(mf.applied_b(None))

    def test_trade_mark_and_priced(self):
        self.assertEqual(mf.trade_mark({"_moss_first": {"arm": "B", "applied": [1]}}), "B")
        for o in ({"_moss_first": {"arm": "B", "applied": []}}, {"_moss_first": {"arm": "A", "applied": []}}, {}, None):
            self.assertIsNone(mf.trade_mark(o))
        trades = [{"price": 1, "moss_first": "B"}, {"price": 2, "moss_first": None}, {"price": 3}]
        self.assertEqual([t["price"] for t in market_buyer.priced(trades)], [2, 3])

    def test_recent_asks_leave_it_out(self):
        md = tempfile.mkdtemp()
        now = 1_794_300_000
        row = lambda price, **kw: {"ts": now - 60, "sku": "G4a74caa4", "asks": [price + 10], "price": price, "quotes": [], **kw}
        with unittest.mock.patch.object(market_buyer, "recent_orders",
                                        return_value=[row(100, **self.B), row(200), row(300, moss_first={"arm": "A", "applied": []})]):
            self.assertEqual(sorted(market_buyer.recent_asks(md, now)["G4a74caa4"]), [200, 210, 300, 310])

    def test_shop_profile_and_rank_pg_leave_it_out(self):
        import buyer_rank_pg
        import buyer_shop_profile
        t0 = 1_794_300_000
        row = {"ts": t0 - 600, "order": "o-1", "shop": "a", "kind": "goods", "quotes": [{"shop": "a", "ask": 1000}],
               "asks": [1000], "state": "TASK_STATE_COMPLETED", "price": 900, "passed_to_kit": True,
               "seconds": {"quotes": 5.0, "end": 15.0}}
        a = buyer_rank_pg.stats_from_rows([dict(row, moss_first={"arm": "A", "applied": []})], {"o-1": "confirmed"}, t0)
        b = buyer_rank_pg.stats_from_rows([dict(row, **self.B)], {"o-1": "confirmed"}, t0)
        self.assertEqual(b["counts"]["excluded_rows"], a["counts"]["excluded_rows"] + 1)
        prow = {"ts": t0 - 600, "asks": [1000], "limit": 930, "shop": "a", "state": "TASK_STATE_CANCELED", "price": None}
        self.assertEqual(buyer_shop_profile.profile_from_rows([dict(prow)] * 40, {}, t0), {"a": 107})
        self.assertEqual(buyer_shop_profile.profile_from_rows([dict(prow, **self.B)] * 40, {}, t0), {})


class Wiring(unittest.TestCase):
    def test_moss_quote_predicate_is_market_buyers(self):
        self.assertTrue(market_buyer.also_in_stock_only(G.strip(), "G4a74caa4", 518149))

    def test_registered_with_the_meta_harness_switches(self):
        import meta_harness
        self.assertIn(".market/buyer_moss_first.json", meta_harness.SWITCHES)
        self.assertIn(".market/buyer_final_m1.json", meta_harness.SWITCHES)


class Wired(unittest.TestCase):
    """market_buyer.moss_first_and_final_m1, which main's price_ladder calls right after the oneshot turn."""

    def setUp(self):
        self.md = tempfile.mkdtemp()
        write(self.md, start=1, end=1 + 9 * 3600)

    def call(self, order, text=G, history=(), d=None, q=None, limit=600000):
        return market_buyer.moss_first_and_final_m1(d, None, text, list(history), limit, (.9,),
                                                    q or {"ask": 518149, "ask_src": "text"}, True, "G4a74caa4", order,
                                                    self.md, "moss-and-oak")

    def order(self, arm, **kw):
        o = {"kind": "goods", "order": order_in(arm), **kw}
        o["_moss_first"] = mf.assign(self.md, o, now=2)
        return o

    def test_off_is_the_turn_as_given(self):
        for d in (None, {"shop_price": 1, "action": "walk_away", "price": 0}):
            self.assertIs(self.call({"kind": "goods"}, d=d), d)

    def test_arm_b_prices_the_moss_quote_arm_a_notes_it(self):
        b, a = self.order("B"), self.order("A")
        with unittest.mock.patch.object(mf.time, "time", return_value=3):
            self.assertEqual(self.call(b), {"shop_price": 518149, "action": "offer", "price": int(.9 * 518149)})
            self.assertIsNone(self.call(a))
        self.assertEqual((len(b["_moss_first"]["applied"]), len(a["_moss_first"]["seen"]), a["_moss_first"]["applied"]),
                         (1, 1, []))

    def test_another_quote_or_a_rank_ask_is_left_alone(self):
        o = self.order("B")
        with unittest.mock.patch.object(mf.time, "time", return_value=3):
            self.assertIsNone(self.call(o, q={"ask": 518149, "ask_src": "text_rank"}))
            self.assertIsNone(self.call(o, q={"ask": 500000, "ask_src": "text"}))
        self.assertEqual(o["_moss_first"]["seen"], [])

    def test_final_m1_is_a_shadow_it_notes_and_never_sends(self):
        m1_write(self.md)
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW):
            self._shadow()

    def _shadow(self):
        walk = oneshot.code_turn(9600, FinalM1.H, 10000, (.9,))
        self.assertIs(self.call({"kind": "food"}, "Final price: 9600. ", FinalM1.H, walk, limit=10000), walk)
        on = {"kind": "food", "_final_m1": [], "_final_m1_window": W1}
        self.assertIs(self.call(on, "Final price: 9600. ", FinalM1.H, walk, limit=10000), walk)
        self.assertEqual(on["_final_m1"], [{"shop": "moss-and-oak", "baseline": "walk_away", "would_offer": 9600,
                                            "final_price": 9600, "last_offer": 9600, "offers": 3, "steps": 1,
                                            "limit": 10000}])
        other = {"kind": "food", "_final_m1": [], "_final_m1_window": W1}
        self.assertIs(self.call(other, "Final price: 9600. ", FinalM1.H[:4], walk, limit=10000), walk)
        self.assertEqual(other["_final_m1"], [])

    def test_main_price_ladder_calls_it_only_when_on(self):
        import test_buyer_accept_band as tb
        history = [("shop asked", 11000), ("you offered", 9900)]
        with unittest.mock.patch.object(market_buyer, "moss_first_and_final_m1", side_effect=AssertionError("called")):
            ladder = tb.RealLadderTest("test_on_takes_the_final_ask_within_105_after_my_raise_off_walks").ladder(
                {"kind": "goods"}, 10000)
            self.assertEqual(ladder(10500, "", history)["price"], 10000)
        seen = []
        with unittest.mock.patch.object(market_buyer, "moss_first_and_final_m1",
                                        side_effect=lambda d, *a: seen.append((d, a[-1])) or d), \
                unittest.mock.patch.object(market_buyer, "md", self.md, create=True):
            ladder = tb.RealLadderTest("test_on_takes_the_final_ask_within_105_after_my_raise_off_walks").ladder(
                {"kind": "goods", "_final_m1": []}, 10000)
            self.assertEqual(ladder(10500, "", history)["price"], 10000)
        self.assertEqual(seen, [({"shop_price": 10500, "action": "offer", "price": 10000}, "fixture")])  # shop["name"]


class FinalM1(unittest.TestCase):
    """oneshot.code_turn walks once the steps and both last offers are spent, before it looks at an ask at or under my
    last offer; a text-only "Final price: N" at my last offer then ends the talk (GPT Pro mw9q5kxg §1.5, §4.1 M1)."""
    H = [("shop asked", 12000), ("you offered", 9000), ("shop asked", 9500), ("you offered", 9500),
         ("shop asked", 9800), ("you offered", 9600)]

    def test_code_turn_walks_there(self):
        self.assertEqual(oneshot.code_turn(9600, self.H, 10000, (0.9,))["action"], "walk_away")

    def test_taken_when_on(self):
        d = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        self.assertEqual(oneshot.final_m1(d, None, "Final price: 9600. ", self.H, 10000, (0.9,)),
                         {"shop_price": 9600, "action": "offer", "price": 9600})
        self.assertEqual(oneshot.final_m1(d, None, "Two offers left. Final price: 9600.", self.H, 10000, (0.9,))["action"],
                         "offer")

    def test_only_that_case(self):
        d = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        own = self.H[:-1] + [("you offered", 9800)]
        for args in ((d, 9600, "Final price: 9600. ", self.H, 10000),            # a metadata ask
                     (d, None, "Counteroffer: 9600", self.H, 10000),             # not Final price
                     (d, None, "Final price: 9600. ", self.H, 9500),             # over my limit now
                     (d, None, "Final price: 9600. ", self.H[:4], 10000),        # offers not spent: code_turn's own path
                     (oneshot.code_turn(9800, own, 10000, (0.9,)), None, "Final price: 9800. ", own, 10000),  # its own ask
                     ({"shop_price": 9600, "action": "offer", "price": 9600}, None, "Final price: 9600. ", self.H, 10000),
                     (None, None, "Final price: 9600. ", self.H, 10000)):
            self.assertEqual(oneshot.final_m1(*args, (0.9,)), args[0], args[1:3])
        lower = {"shop_price": 9500, "action": "walk_away", "price": 0}
        self.assertEqual(oneshot.final_m1(lower, None, "Final price: 9500. ", self.H, 10000, (0.9,)), lower)

    def test_switch(self):
        md = tempfile.mkdtemp()
        self.assertFalse(oneshot.final_m1_on(md, now=NOW))
        for body in ('{"enabled": false}', "{bad", '{"enabled": "true"}', "[]"):
            with open(os.path.join(md, oneshot.FINAL_M1_SWITCH), "w") as f:
                f.write(body)
            self.assertFalse(oneshot.final_m1_on(md, now=NOW), body)
        m1_write(md)
        self.assertTrue(oneshot.final_m1_on(md, now=NOW))
        self.assertFalse(oneshot.final_m1_on(md, env={"BUYER_FINAL_M1": "0"}, now=NOW))

    def test_switch_needs_a_finite_window_written_before_it(self):
        # ax-pro-cycle step 5 / main instruction 10: a shadow with no end ran until someone turned it off by hand.
        md = tempfile.mkdtemp()
        with open(os.path.join(md, oneshot.FINAL_M1_SWITCH), "w") as f:
            f.write('{"enabled": true}')                     # the old form, no window: now off
        self.assertFalse(oneshot.final_m1_on(md, now=NOW))
        for start, end in ((NOW - 60, NOW - 60),                     # empty
                           (NOW - 60, NOW - 120),                    # ends before it starts
                           (NOW - 60, NOW - 60 + 12 * 3600 + 1),     # over the 12 h maximum
                           (NOW + 60, NOW + 1800),                   # not started
                           (NOW - 1800, NOW - 60),                   # over
                           (NOW - 60, float("inf")), (float("nan"), NOW + 60), (True, NOW + 60), (NOW - 60, 10 ** 400),
                           ("1", NOW + 60), (None, NOW + 60), (0, NOW + 60), (-5, NOW + 60)):
            m1_write(md, start=start, end=end)
            self.assertFalse(oneshot.final_m1_on(md, now=NOW), (start, end))
        m1_write(md, start=NOW - 60, end=NOW - 60 + 12 * 3600)
        self.assertTrue(oneshot.final_m1_on(md, now=NOW))
        m1_write(md, mtime=NOW - 30)                                 # written after its start
        self.assertFalse(oneshot.final_m1_on(md, now=NOW))
        m1_write(md)                                                 # start NOW - 60, end NOW - 60 + 1800
        self.assertTrue(oneshot.final_m1_on(md, now=NOW - 60))      # the start is in
        self.assertFalse(oneshot.final_m1_on(md, now=NOW - 61))
        self.assertTrue(oneshot.final_m1_on(md, now=NOW - 60 + 1799))
        self.assertFalse(oneshot.final_m1_on(md, now=NOW - 60 + 1800))   # the end is out
        for now in (float("nan"), "x", True):
            self.assertFalse(oneshot.final_m1_on(md, now=now), now)
        # now=None reads the clock: the clock is injected, so the test does not fail at real 17:39~18:09 11/10 (NOW's window)
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW - 61):
            self.assertFalse(oneshot.final_m1_on(md, now=None))
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW):
            self.assertTrue(oneshot.final_m1_on(md, now=None))
            self.assertTrue(oneshot.final_m1_on(md))
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW + 1800):
            self.assertFalse(oneshot.final_m1_on(md))

    def test_the_shadow_stops_noting_at_the_window_end(self):
        # An order started inside the window notes nothing once the window is over or the switch is off.
        md = tempfile.mkdtemp()
        m1_write(md)
        walk = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        on = {"kind": "food", "_final_m1": [], "_final_m1_window": W1}
        call = lambda: market_buyer.moss_first_and_final_m1(walk, None, "Final price: 9600. ", list(self.H), 10000,
                                                             (0.9,), {}, False, "G1", on, md, "kimauve")
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW - 60 + 1800):
            self.assertIs(call(), walk)
        self.assertEqual(on["_final_m1"], [])
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW):
            self.assertIs(call(), walk)
        self.assertEqual(len(on["_final_m1"]), 1)
        with open(os.path.join(md, oneshot.FINAL_M1_SWITCH), "w") as f:
            f.write('{"enabled": false}')
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW):
            self.assertIs(call(), walk)
        self.assertEqual(len(on["_final_m1"]), 1)

    def test_an_order_notes_only_in_the_window_it_started_in(self):          # GPT Pro mwa9krfw M-1
        md = tempfile.mkdtemp()
        m1_write(md)
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW):
            self.assertEqual(oneshot.final_m1_window(md), W1)
            self.assertTrue(oneshot.final_m1_on(md))
        walk = oneshot.code_turn(9600, self.H, 10000, (0.9,))
        on = {"kind": "food", "_final_m1": [], "_final_m1_window": W1}
        call = lambda: market_buyer.moss_first_and_final_m1(walk, None, "Final price: 9600. ", list(self.H), 10000,
                                                             (0.9,), {}, False, "G1", on, md, "kimauve")
        m1_write(md, start=NOW + 1800, end=NOW + 3600, mtime=NOW - 120)       # window B, the order is from A
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW + 1900):
            self.assertTrue(oneshot.final_m1_on(md))
            self.assertIs(call(), walk)
        self.assertEqual(on["_final_m1"], [])
        old = {"kind": "food", "_final_m1": []}                              # resumed from before: no window kept
        with unittest.mock.patch.object(oneshot.time, "time", return_value=NOW + 1900):
            market_buyer.moss_first_and_final_m1(walk, None, "Final price: 9600. ", list(self.H), 10000,
                                                 (0.9,), {}, False, "G1", old, md, "kimauve")
        self.assertEqual(old["_final_m1"], [])


if __name__ == "__main__":
    unittest.main()
