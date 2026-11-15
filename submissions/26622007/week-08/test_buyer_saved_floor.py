"""buyer_saved_floor: the limit keeps my current Saved, not the fixed 0.30 (W-20261109-1638-AXPro주기)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import buyer_saved_floor as sf
import market_buyer as mb


def board(md, saved, ts=None):
    with open(os.path.join(md, "board_history.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts if ts is not None else time.time(), "buyers": {"nocha12": [saved, 0.89, 0.79]}}) + "\n")


def switch(md, **kw):
    """The switch on now: a window [now - 60 s, now + 1 h) unless the test gives its own start_at/stop_at."""
    now = time.time()
    with open(os.path.join(md, sf.SWITCH), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "start_at": now - 60, "stop_at": now + 3600, **kw}, f)


def raw_switch(md, text):
    with open(os.path.join(md, sf.SWITCH), "w", encoding="utf-8") as f:
        f.write(text)


class Share(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.md = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def test_off_without_switch(self):
        board(self.md, 0.3096)
        self.assertIsNone(sf.state(self.md, "o-1"))

    def test_off_by_env(self):
        board(self.md, 0.3096)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={"BUYER_SAVED_FLOOR": "0"}))

    def test_not_enabled_true_is_off(self):
        board(self.md, 0.3096)
        with open(os.path.join(self.md, sf.SWITCH), "w") as f:
            json.dump({"enabled": "true"}, f)
        self.assertIsNone(sf.state(self.md, "o-1"))

    def test_on_uses_board_saved(self):
        board(self.md, 0.3096)
        switch(self.md)
        st = sf.state(self.md, "o-1", env={}, base=0.30)  # legacy study uses its explicit base
        self.assertEqual(st["arm"], "B")
        self.assertAlmostEqual(st["share"], 0.3096)
        self.assertTrue(st["band_off"])

    def test_margin_and_bounds(self):
        board(self.md, 0.3096)
        switch(self.md, margin=0.002)
        self.assertAlmostEqual(sf.state(self.md, "o-1", env={}, base=0.30)["share"], 0.3116)
        switch(self.md, margin=0.5)            # out of range: off (review I2)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))
        board(self.md, 0.62)                   # a board Saved over MAX_SHARE is not trusted: off (review N6)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_bad_config_is_off(self):        # review I2: a broken setting never turns the floor on
        board(self.md, 0.3096)
        for bad in ({"ab": None}, {"band": "Keep"}, {"margin": "0.01"}, {"kinds": None}, {"unknown": 1}, {"stop_at": 1}):
            switch(self.md, **bad)
            self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="food"), bad)

    def test_malformed_board_is_off(self):   # review I1: never raises into the buyer run
        switch(self.md)
        for line in ({"ts": time.time(), "buyers": [1]}, {"ts": time.time(), "buyers": {"nocha12": "x"}}, [1, 2], "s"):
            with open(os.path.join(self.md, "board_history.jsonl"), "w") as f:
                f.write(json.dumps(line) + "\n")
            self.assertIsNone(sf.state(self.md, "o-1", env={}), line)
        with open(os.path.join(self.md, "board_history.jsonl"), "wb") as f:
            f.write(b"\xff\xfe{bad\n")
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_public_default_is_033_even_when_board_is_lower(self):
        board(self.md, 0.3096)
        switch(self.md)
        self.assertEqual(sf.state(self.md, "o-1", env={})["share"], 0.33)

    def test_never_under_base(self):
        board(self.md, 0.25)
        switch(self.md)
        self.assertEqual(sf.state(self.md, "o-1", env={}, base=mb.SAVED_MIN_SHARE)["share"], mb.SAVED_MIN_SHARE)

    def test_stale_board_is_off(self):
        board(self.md, 0.3096, ts=time.time() - 4 * 3600)
        switch(self.md)
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_band_kept_when_asked(self):
        board(self.md, 0.3096)
        switch(self.md, band="keep")
        self.assertFalse(sf.state(self.md, "o-1", env={})["band_off"])

    def test_ab_split_is_by_order_hash(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "sf-test", "share": 0.5})
        arms = [sf.state(self.md, f"o-{i}", env={})["arm"] for i in range(400)]
        self.assertTrue(150 < arms.count("B") < 250)
        self.assertEqual(arms, [sf.state(self.md, f"o-{i}", env={})["arm"] for i in range(400)])
        a = next(sf.state(self.md, f"o-{i}", env={}, base=mb.SAVED_MIN_SHARE) for i in range(400) if arms[i] == "A")
        self.assertEqual(a["share"], mb.SAVED_MIN_SHARE)
        self.assertFalse(a["band_off"])

    def test_kinds_limits_the_floor(self):
        board(self.md, 0.3096)
        switch(self.md, kinds=["food"])
        self.assertEqual(sf.state(self.md, "o-1", env={}, kind="food")["arm"], "B")
        self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="goods"))
        self.assertIsNone(sf.state(self.md, "o-1", env={}))          # kind unknown: off
        switch(self.md, kinds="food")                                 # not a list: off
        self.assertIsNone(sf.state(self.md, "o-1", env={}, kind="food"))

    def test_band_only_keeps_base_share(self):   # GPT Pro 1638 §2.4: the first A/B is the band off alone
        board(self.md, 0.3096)
        switch(self.md, floor=False)
        st = sf.state(self.md, "o-1", env={}, base=mb.SAVED_MIN_SHARE)
        self.assertEqual((st["arm"], st["share"], st["band_off"]), ("B", mb.SAVED_MIN_SHARE, True))
        switch(self.md, floor=False, band="keep")      # nothing would change: off
        self.assertIsNone(sf.state(self.md, "o-1", env={}))
        switch(self.md, floor="no")
        self.assertIsNone(sf.state(self.md, "o-1", env={}))

    def test_empty_order_id_is_off_under_ab(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "s", "share": 0.5})
        self.assertIsNone(sf.state(self.md, None, env={}))
        self.assertIsNone(sf.state(self.md, "", env={}))

    def test_ab_bad_share_is_off(self):
        board(self.md, 0.3096)
        switch(self.md, ab={"salt": "x", "share": 2})
        self.assertIsNone(sf.state(self.md, "o-1", env={}))


class Window(unittest.TestCase):
    """A finite window [start_at, stop_at) of at most MAX_WINDOW_HOURS in the switch itself (W-20261109-1638 지시 3):
    the floor ends at stop_at by the code, not by the experiments registry or a reminder. Missing, not a finite number
    (bool, string, NaN, inf), reversed, too long, before start_at and from stop_at on: off."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.md = self.d.name
        self.t0 = time.time()
        board(self.md, 0.3096, ts=self.t0)

    def tearDown(self):
        self.d.cleanup()

    def on(self, now=None, **kw):
        cfg = {"enabled": True, "floor": False, "ab": {"salt": "sf-w", "share": 1.0}, **kw}
        raw_switch(self.md, json.dumps(cfg))
        return sf.state(self.md, "o-1", env={}, now=self.t0 if now is None else now)

    def test_missing_window_is_off(self):
        self.assertIsNone(self.on())
        self.assertIsNone(self.on(start_at=self.t0 - 60))
        self.assertIsNone(self.on(stop_at=self.t0 + 60))
        self.assertIsNone(self.on(start_at=None, stop_at=None))

    def test_not_a_finite_number_is_off(self):
        good = {"start_at": self.t0 - 60, "stop_at": self.t0 + 3600}
        for k in ("start_at", "stop_at"):
            for bad in (True, False, str(good[k]), [good[k]], {"t": good[k]}):
                self.assertIsNone(self.on(**{**good, k: bad}), (k, bad))
        for k in ("start_at", "stop_at"):
            for token in ("NaN", "Infinity", "-Infinity"):
                text = json.dumps({"enabled": True, "floor": False, **good}).replace(json.dumps(good[k]), token)
                raw_switch(self.md, text)
                self.assertIsNone(sf.state(self.md, "o-1", env={}, now=self.t0), (k, token))
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False, **good}))   # the same switch, finite: on
        self.assertEqual(sf.state(self.md, "o-1", env={}, now=self.t0)["arm"], "B")

    def test_int_out_of_float_range_is_off_not_a_crash(self):   # parent 02:30: math.isfinite(10**400) raises
        big = 10 ** 400
        for kw in ({"start_at": big, "stop_at": big + 1}, {"start_at": self.t0 - 60, "stop_at": big},
                   {"start_at": -big, "stop_at": self.t0 + 60},
                   {"start_at": self.t0 - 60, "stop_at": self.t0 + 60, "margin": big},
                   {"start_at": self.t0 - 60, "stop_at": self.t0 + 60, "ab": {"salt": "s", "share": big}}):
            raw_switch(self.md, json.dumps({"enabled": True, "floor": False, **kw}))
            self.assertIsNone(sf.config(self.md, env={}, now=self.t0), kw)
            self.assertIsNone(sf.state(self.md, "o-1", env={}, now=self.t0), kw)
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False, "start_at": self.t0 - 60, "stop_at": self.t0 + 60}))
        self.assertIsNone(sf.config(self.md, env={}, now=big))
        self.assertIsNone(sf.board_row(self.md, now=big))            # delta review 2 M-2
        self.assertIsNone(sf.board_saved(self.md, now=float("nan")))

    def test_reversed_empty_too_long_or_not_positive_is_off(self):
        self.assertIsNone(self.on(start_at=self.t0 + 60, stop_at=self.t0 - 60))
        self.assertIsNone(self.on(start_at=self.t0, stop_at=self.t0))
        self.assertIsNone(self.on(start_at=self.t0 - 60, stop_at=self.t0 - 60 + sf.MAX_WINDOW_HOURS * 3600 + 1))
        self.assertIsNotNone(self.on(start_at=self.t0 - 60, stop_at=self.t0 - 60 + sf.MAX_WINDOW_HOURS * 3600))
        for s in (0, -60):                     # config alone: a board for that now does not exist
            raw_switch(self.md, json.dumps({"enabled": True, "floor": False, "start_at": s, "stop_at": 3600}))
            self.assertIsNone(sf.config(self.md, env={}, now=10), s)
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False, "start_at": 1, "stop_at": 3600}))
        self.assertIsNotNone(sf.config(self.md, env={}, now=10))

    def test_before_start_and_from_stop_on_is_off(self):
        s, e = self.t0, self.t0 + 9 * 3600
        self.assertIsNone(self.on(now=s - 1, start_at=s, stop_at=e))
        self.assertEqual(self.on(now=s, start_at=s, stop_at=e)["arm"], "B")
        board(self.md, 0.3096, ts=e - 1)
        self.assertEqual(self.on(now=e - 1, start_at=s, stop_at=e)["arm"], "B")
        board(self.md, 0.3096, ts=e)
        self.assertIsNone(self.on(now=e, start_at=s, stop_at=e))
        self.assertIsNone(self.on(now=e + 1, start_at=s, stop_at=e))

    def test_default_now_is_the_clock(self):
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False,
                                        "start_at": self.t0 - 7200, "stop_at": self.t0 - 3600}))
        self.assertIsNone(sf.state(self.md, "o-1", env={}))          # ended an hour ago: off with no now given
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False,
                                        "start_at": self.t0 + 3600, "stop_at": self.t0 + 7200}))
        self.assertIsNone(sf.state(self.md, "o-1", env={}))          # not started: off

    def test_both_arms_record_the_window(self):
        s, e = self.t0 - 60, self.t0 + 3600
        raw_switch(self.md, json.dumps({"enabled": True, "floor": False, "ab": {"salt": "sf-w", "share": 0.5},
                                        "start_at": s, "stop_at": e}))
        rows = [sf.state(self.md, f"o-{i}", env={}, now=self.t0) for i in range(40)]
        self.assertEqual({r["arm"] for r in rows}, {"A", "B"})
        for r in rows:
            self.assertEqual((r["start_at"], r["stop_at"], r["v"]), (s, e, sf.VERSION))

    def test_off_after_the_window_is_the_old_behaviour(self):
        """After stop_at an order is not in the study at all: no arm, the old share and band (state None)."""
        s, e = self.t0 - 3600, self.t0 - 1
        self.assertIsNone(self.on(now=self.t0, start_at=s, stop_at=e))


class SwitchOn(unittest.TestCase):
    """The one writer of the switch: always a finite window, written whole (0600), never over another study's open one."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.md = self.d.name
        self.t0 = time.time()
        board(self.md, 0.3096, ts=self.t0)

    def tearDown(self):
        self.d.cleanup()

    def path(self):
        return os.path.join(self.md, sf.SWITCH)

    def test_writes_a_finite_band_only_window(self):
        d = sf.switch_on(self.md, salt="sf2w-test", start_at=self.t0, hours=12, by="W", why="t", now=self.t0)
        self.assertEqual((d["start_at"], d["stop_at"]), (self.t0, self.t0 + 12 * 3600))
        self.assertEqual((d["floor"], d["band"], d["ab"]), (False, "cap", {"salt": "sf2w-test", "share": 0.5}))
        self.assertNotIn("kinds", d)                                   # food and goods both
        self.assertEqual(os.stat(self.path()).st_mode & 0o777, 0o600)
        with open(self.path(), encoding="utf-8") as f:
            self.assertEqual(json.load(f), d)
        self.assertIsNotNone(sf.config(self.md, env={}, now=self.t0))
        self.assertIsNone(sf.config(self.md, env={}, now=self.t0 + 12 * 3600))
        self.assertIsNone(sf.config(self.md, env={}, now=self.t0 - 1))

    def test_bad_values_raise_and_leave_the_old_file(self):
        raw_switch(self.md, '{"enabled": false}')
        for kw in ({"hours": 12.01}, {"hours": 0}, {"hours": float("nan")}, {"hours": True}, {"start_at": float("inf")},
                   {"start_at": "1"}, {"start_at": None}, {"salt": ""}, {"share": 0}, {"share": 1.5}, {"floor": "no"},
                   {"band": "Keep"}, {"floor": False, "band": "keep"}, {"kinds": ["toys"]}, {"by": {1}},
                   {"why": 3}, {"start_at": 10 ** 400}, {"hours": 10 ** 400}, {"share": 10 ** 400},
                   {"hours": 10 ** 305}, {"hours": 1e300}):
            args = {"salt": "sf2w-test", "start_at": self.t0, "hours": 9, "now": self.t0, **kw}
            with self.assertRaises(ValueError, msg=kw):
                sf.switch_on(self.md, **args)
            with open(self.path(), encoding="utf-8") as f:
                self.assertEqual(f.read(), '{"enabled": false}', kw)
        self.assertEqual([n for n in os.listdir(self.md) if ".tmp" in n], [])

    def test_never_over_another_open_or_unknown_study(self):
        sf.switch_on(self.md, salt="theirs", start_at=self.t0, hours=12, now=self.t0)
        with open(self.path(), encoding="utf-8") as f:
            before = f.read()
        with self.assertRaises(ValueError):
            sf.switch_on(self.md, salt="mine", start_at=self.t0, hours=9, now=self.t0 + 60)
        raw_switch(self.md, json.dumps({"enabled": True, "floor": True}))   # an old, windowless dynamic floor: unknown
        with self.assertRaises(ValueError):
            sf.switch_on(self.md, salt="mine", start_at=self.t0, hours=9, now=self.t0)
        raw_switch(self.md, before)
        d = sf.switch_on(self.md, salt="mine", start_at=self.t0 + 12 * 3600, hours=9, now=self.t0 + 12 * 3600)
        self.assertEqual(d["ab"]["salt"], "mine")                       # theirs ended at its stop_at: may follow
        raw_switch(self.md, '{"enabled": false}')
        self.assertEqual(sf.switch_on(self.md, salt="x", start_at=self.t0, hours=9, now=self.t0)["ab"]["salt"], "x")

    def test_unreadable_old_file_is_not_overwritten(self):     # review M-1
        for text in ('{"enabled": true, "floor": tr', "\xff", '{"enabled": true, "start_at": NaN}x'):
            raw_switch(self.md, text)
            with self.assertRaises(ValueError, msg=text):
                sf.switch_on(self.md, salt="mine", start_at=self.t0, hours=9, now=self.t0)
            with open(self.path(), encoding="utf-8") as f:
                self.assertEqual(f.read(), text)

    def test_written_switch_turns_arm_b_band_off(self):
        sf.switch_on(self.md, salt="sf2w-test", start_at=self.t0 - 60, hours=9, share=1.0, now=self.t0)
        st = sf.state(self.md, "o-1", env={}, now=self.t0, base=mb.SAVED_MIN_SHARE)
        self.assertEqual((st["arm"], st["share"], st["band_off"], st["floor"]), ("B", mb.SAVED_MIN_SHARE, True, False))


class Learning(unittest.TestCase):
    """Arm B's trades and talks are left out of what both arms learn from (GPT Pro 1638 P1)."""

    def test_priced_leaves_out_arm_b(self):
        trades = [{"order": "1", "saved_floor": "B"}, {"order": "2", "saved_floor": "A"}, {"order": "3"},
                  {"order": "4", "saved_floor": None}]
        self.assertEqual([t["order"] for t in sf.priced(trades)], ["2", "3", "4"])
        self.assertEqual([t["order"] for t in mb.priced(trades)], ["2", "3", "4"])

    def test_rows_and_trade_arm(self):
        self.assertTrue(sf.floored({"saved_floor": {"arm": "B"}}))
        self.assertFalse(sf.floored({"saved_floor": {"arm": "A"}}))
        self.assertFalse(sf.floored({}))
        self.assertEqual(sf.trade_arm({"_saved_floor": {"arm": "B"}}), "B")
        self.assertIsNone(sf.trade_arm({}))

    def test_recent_asks_skip_arm_b_talk_asks_and_prices(self):   # review 4f28644 I1: hopeless() reads it
        now = time.time()
        rows = [{"ts": now - 60, "sku": "G1", "asks": [9000], "price": 8800, "saved_floor": {"arm": "B"},
                 "quotes": [{"shop": "s", "sku": "G1", "ask": 9500, "lost": None}]},
                {"ts": now - 60, "sku": "G1", "asks": [9900], "price": 9700, "saved_floor": {"arm": "A"}, "quotes": []}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
            self.assertEqual(sorted(mb.recent_asks(md, now=now)["G1"]), [9500, 9700, 9900])

    def test_learners_skip_arm_b_rows(self):
        import buyer_rank_pg, buyer_shop_profile
        for mod in (buyer_rank_pg, buyer_shop_profile):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            self.assertIn("buyer_saved_floor.floored(r)", src, mod.__name__)
        src = Path(mb.__file__).read_text(encoding="utf-8")
        self.assertIn('"saved_floor": buyer_saved_floor.trade_arm(order)', src)


class Cap(unittest.TestCase):
    def tearDown(self):
        mb.SAVED_SHARE = mb.SAVED_MIN_SHARE

    def test_saved_cap_default_follows_saved_share(self):
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), 6799)
        mb.SAVED_SHARE = 0.31
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0), 6899)  # existing float truncation, never rounded up
        self.assertEqual(mb.saved_cap({"list_price": 10000}, 0, 0.30), 7000)   # an explicit share still wins

    def test_main_sets_and_logs_the_floor(self):
        src = Path(mb.__file__).read_text(encoding="utf-8")
        i = src.index("def main():")
        self.assertIn("buyer_saved_floor.state(md", src[i:])
        self.assertIn('kind=order.get("kind")', src[i:])
        self.assertIn("SAVED_SHARE = ", src[i:])
        j = src.index("band_base, band_pct = accept_percent(", i)
        self.assertIn("band_off", src[j:j + 600])


class Assignment(unittest.TestCase):
    """Every arm record names its study, version, board snapshot and floor flag, so the judgement can recount the arms
    by sha1(salt|order) over every order (GPT Pro 1638 P2 buyer_saved_floor.py:76-98, :917)."""

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.md = self.d.name

    def tearDown(self):
        self.d.cleanup()

    def test_both_arms_carry_study_version_board_and_floor(self):
        ts = time.time() - 60
        board(self.md, 0.3096, ts=ts)
        switch(self.md, floor=False, ab={"salt": "sf-test", "share": 0.5})
        rows = [sf.state(self.md, f"o-{i}", env={}) for i in range(40)]
        self.assertEqual({r["arm"] for r in rows}, {"A", "B"})
        for r in rows:
            self.assertEqual((r["study"], r["v"], r["floor"]), ("sf-test", sf.VERSION, False))
            self.assertAlmostEqual(r["board_ts"], ts)
            self.assertEqual(r["board_saved"], 0.3096)

    def test_without_ab_the_study_is_none(self):
        board(self.md, 0.3096)
        switch(self.md)
        st = sf.state(self.md, "o-1", env={})
        self.assertIsNone(st["study"])
        self.assertTrue(st["floor"])

    def test_recheck_only_for_arm_b_with_the_floor(self):
        self.assertTrue(sf.recheck({"arm": "B", "floor": True}))
        self.assertFalse(sf.recheck({"arm": "B", "floor": False}))    # band off alone: the first A/B stays the band
        self.assertFalse(sf.recheck({"arm": "A", "floor": True}))
        self.assertFalse(sf.recheck(None))
        self.assertFalse(sf.recheck({"arm": "B"}))                     # an old record without the flag: no change

    def test_no_shop_in_stock_row_carries_the_arm(self):
        """The early exit (no shop has the order in stock) writes its orders.jsonl row with the arm, so the arm's
        denominator keeps every order it was given (P2: the early exits were left out)."""
        import contextlib
        import io
        import sys
        board(self.md, 0.3096)
        switch(self.md, floor=False, ab={"salt": "sf-test", "share": 1.0})
        os.makedirs(os.path.join(self.md, "measure"), exist_ok=True)
        with open(os.path.join(self.md, "catalog.json"), "w") as f:
            json.dump({"G1": {"list_price": 10000, "name": "x", "category": "c"}}, f)
        shops = os.path.join(self.md, "shops.json")
        with open(shops, "w") as f:
            json.dump([{"name": "s1", "url": "http://127.0.0.1:9/", "stock": []}], f)
        order = os.path.join(self.md, "order.json")
        with open(order, "w") as f:
            json.dump({"order": "o-early", "kind": "food", "category": "c", "item": "G1", "acceptable": ["G1"]}, f)
        argv, mem, aux = sys.argv, mb.MEMORY, mb.AGENT_DIR
        sys.argv = ["market_buyer.py", "--shops", shops, "--item", "G1", "--budget", "9000", "--order", order]
        mb.MEMORY = os.path.join(self.md, "mem.json")
        env = {k: v for k, v in os.environ.items() if k != "BUYER_SAVED_FLOOR"}   # an outside off switch: not this test
        try:
            with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(mb.main(), 0)
        finally:
            sys.argv, mb.MEMORY, mb.AGENT_DIR = argv, mem, aux
            mb.SAVED_SHARE = mb.SAVED_MIN_SHARE
        self.assertIn('"receipt": null', out.getvalue())
        with open(os.path.join(self.md, "measure", "orders.jsonl")) as f:
            rows = [json.loads(l) for l in f]
        self.assertEqual(rows[-1]["state"], "NO_SHOP_IN_STOCK")
        self.assertEqual(rows[-1]["saved_floor"]["arm"], "B")
        self.assertEqual(rows[-1]["saved_floor"]["study"], "sf-test")


class ShippedSku(unittest.TestCase):
    """A receipt for another SKU than the one asked: its saved cap used b = 0 (GPT Pro 1638 P2 market_buyer.py:1689-1707).
    Under the floor (arm B, floor true) it uses that SKU's break chance; off and band-only arms keep b = 0."""

    def tearDown(self):
        mb.SAVED_SHARE = mb.SAVED_MIN_SHARE

    def test_shipped_chance_lowers_the_cap(self):
        offer = {"sku": "G1", "list_price": 100000}
        catalog = {"G2": {"list_price": 100000}}
        rec = {"item": "G2", "price": 66000}
        mb.SAVED_SHARE = 0.31
        self.assertIsNone(mb.over_limit(rec, offer, 69000, catalog, 200000))               # b = 0: cap 69,000
        self.assertIsNone(mb.over_limit(rec, offer, 69000, catalog, 200000, shipped_chance=None))
        why = mb.over_limit(rec, offer, 69000, catalog, 200000, shipped_chance=0.13)       # cap 100000 x (1 - .31/.87) = 64,367
        self.assertIsNotNone(why)
        self.assertIn("G2", why)

    def test_same_sku_ignores_shipped_chance(self):
        offer = {"sku": "G1", "list_price": 100000}
        rec = {"item": "G1", "price": 66000}
        self.assertIsNone(mb.over_limit(rec, offer, 69000, {}, 200000, shipped_chance=0.5))

    def test_receipt_chance_only_when_rechecked(self):
        order_b = {"_saved_floor": {"arm": "B", "floor": True}, "kind": "goods"}
        offer = {"sku": "G1", "list_price": 100000}
        chance = lambda sku: 0.15 if sku == "G2" else None   # noqa: E731
        self.assertEqual(mb.receipt_chance(order_b, offer, {"item": "G2"}, chance), 0.15)
        self.assertIsNone(mb.receipt_chance(order_b, offer, {"item": "G1"}, chance))      # the asked SKU: limit as is
        self.assertIsNone(mb.receipt_chance({**order_b, "kind": "food"}, offer, {"item": "G2"}, chance))
        self.assertIsNone(mb.receipt_chance({"_saved_floor": {"arm": "B", "floor": False}, "kind": "goods"},
                                            offer, {"item": "G2"}, chance))
        self.assertIsNone(mb.receipt_chance({"kind": "goods"}, offer, {"item": "G2"}, chance))
        self.assertEqual(mb.receipt_chance(order_b, offer, {"item": "G3"}, chance), 0.0)  # no rate: 0 as before

        self.assertEqual(mb.receipt_chance(order_b, offer, {"item": "G2"}, lambda sku: 1.2), 1.0)   # at most 1: cap 0
        self.assertEqual(mb.receipt_chance(order_b, offer, {"item": "G2"}, lambda sku: float("nan")), 0.0)

        def boom(sku):
            raise ValueError(sku)
        self.assertIsNone(mb.receipt_chance(order_b, offer, {"item": "G2"}, boom))       # never raises into the run

    def test_main_passes_and_records_the_shipped_chance(self):
        """main() hands receipt_chance's result to over_limit and records it in the order's saved_floor (review N1, N3),
        with whether over_limit could use it (the shipped SKU has a list price; delta review N1)."""
        with open(mb.__file__, encoding="utf-8") as f:
            src = f.read()
        i = src.index("def main():")
        j = src.index("shipped = receipt_chance(order, offer, rec,", i)
        k = src.index("over = over_limit(rec, offer, r_limit, catalog, args.budget,", j)   # r_limit: margin cap take
        self.assertIn('order["_saved_floor"]["shipped"] = {"sku": rec.get("item")', src[j:k])
        self.assertIn('"applied": isinstance(lp, (int, float))', src[j:k])
        self.assertIn("shipped)", src[k:k + 300])


if __name__ == "__main__":
    unittest.main()
