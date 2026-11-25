"""seller_food_margin: food reserve with a margin over cost, arms by unit (W-20261109-2040-AX셀러전략)."""
import json
import io
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import profit_price
import seller_food_margin as fd

ON = 1794229200.0          # 11/09 23:20 KST


def unit(**kw):
    # units_in.jsonl 11/09 17:04: grade A 포기김치 10kg, cost .790 of list; our A food sold at .736 of list against
    # .790 at other shops on the same SKU x grade (ledger 11/08 23:40-11/09 22:13)
    return {"sku": "G200e39a3", "unit": "u-e9e1c5b1e5f1", "kind": "food", "grade": "A", "cost": 29924,
            "list_price": 37900, "expires_at": 1794677634.0, **kw}


PRICING = {"price": {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035, "median_up": 1.0,
                     "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344}}}


class SellerFoodMarginTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        d = {"enabled": True, "study": "food-margin-t", "salt": "s1", "share": 0.05, "start_at": ON - 60,
             "stop_at": ON + 3600, **kw}
        Path(self.md.name, fd.SWITCH).write_text(json.dumps(d))

    def salt_for(self, want, u=None):
        u = u or unit()
        return next(f"s{i}" for i in range(200) if fd.arm({"salt": f"s{i}"}, u["unit"]) == want)

    def q(self, u, median=29000):
        return profit_price.quote(u, median=median, pricing=PRICING)

    def run_(self, u, q, keep=1, margin=None, pricing=PRICING, env=None, now=ON):
        return fd.decide(u, q, reserve=q.get("floor"), ask=q.get("open"), keep=keep, margin_assignment=margin,
                         pricing=pricing, md=self.md.name, env=env or {}, now=now)

    def test_today_food_reserve_is_cost(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(q["floor"], 30000)                 # ceil(29924 x 1.0025 + 1): 0.25% over cost
        self.assertEqual(q["open"], 30000)                  # the SKU median is under it: the first ask is the floor

    def test_dose_study_raises_both_arms_and_records_both_shares(self):
        u = unit(cost=6000, list_price=10000)
        q = {"floor": 6016, "open": 6016}
        for arm, share, expected in (("A", 0.10, 6600), ("B", 0.15, 6900)):
            with self.subTest(arm=arm):
                self.on(salt=self.salt_for(arm, u), share=0.15, a_share=0.10)
                reserve, ask, rec = self.run_(u, q)
                self.assertEqual((reserve, ask), (expected, expected))
                self.assertEqual((rec["arm"], rec["share"], rec["a_share"], rec["b_share"]),
                                 (arm, share, 0.10, 0.15))

    def test_dose_study_caps_at_list_and_keeps_higher_today(self):
        for arm in ("A", "B"):
            for cost, floor, opening, expected in ((9500, 9600, 9700, (10000, 10000)),
                                                   (10000, 11000, 12000, (11000, 12000))):
                with self.subTest(arm=arm, cost=cost):
                    u = unit(cost=cost, list_price=10000)
                    self.on(salt=self.salt_for(arm, u), share=0.15, a_share=0.10)
                    self.assertEqual(self.run_(u, {"floor": floor, "open": opening})[:2], expected)

    def test_invalid_dose_switch_falls_back_to_default_or_default_off(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        invalid = [{"a_share": bad, "share": 0.15} for bad in (0.5, "x", True, float("nan"), None)]
        invalid += [{"share": 0.15}, {"share": 0.10, "a_share": 0.10},
                    {"share": 0.05, "a_share": 0.10}, {"share": 0.151, "a_share": 0.10}]
        for settings in invalid:
            with self.subTest(settings=settings):
                self.on(**settings)
                self.assertIsNone(fd.config(self.md.name, env={}, now=ON))
                self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "D")
                self.on(default_off=True, **settings)
                self.assertEqual(self.run_(u, q, pricing=pricing), (30000, 30000, None))

    def test_dose_producer_validates_before_writing_and_preserves_stop(self):
        path = Path(self.md.name, fd.SWITCH)
        invalid = [{"a_share": bad, "share": 0.15} for bad in (0.5, "x", True, float("nan"))]
        invalid += [{"share": 0.15}, {"share": 0.10, "a_share": 0.10},
                    {"share": 0.05, "a_share": 0.10}, {"share": 0.151, "a_share": 0.10}]
        for settings in invalid:
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError):
                    fd.switch_on(self.md.name, study="dose", salt="s", start_at=ON, hours=1, **settings)
                self.assertEqual(list(Path(self.md.name).iterdir()), [])
        fd.default_off(self.md.name)
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            fd.switch_on(self.md.name, study="dose", salt="s", start_at=ON, hours=1, share=0.15)
        self.assertEqual(path.read_bytes(), before)
        d = fd.switch_on(self.md.name, study="dose", salt="s", start_at=ON, hours=1, share=0.15, a_share=0.10)
        self.assertEqual((d["a_share"], d["share"], d["default_off"]), (0.10, 0.15, True))
        self.assertEqual(json.loads(path.read_text()), d)
        self.assertEqual(fd.config(self.md.name, env={}, now=ON)["a_share"], 0.10)

    def test_dose_window_ends_at_configured_default(self):
        u = unit(cost=6000, list_price=10000)
        q = {"floor": 6016, "open": 6016}
        pricing = {"price": {"food_reserve_margin": 0.10}}
        self.on(salt=self.salt_for("B", u), share=0.15, a_share=0.10)
        self.assertEqual(self.run_(u, q, pricing=pricing, now=ON + 3599)[2]["arm"], "B")
        reserve, ask, rec = self.run_(u, q, pricing=pricing, now=ON + 3600)
        self.assertEqual((reserve, ask, rec["arm"], rec["share"]), (6600, 6600, "D", 0.10))
        self.assertEqual(self.run_(u, q, pricing=pricing, env={"SELLER_FOOD_MARGIN": "0"}), (6016, 6016, None))
        reserve, ask, rec = self.run_(u, q, pricing={"price": {"food_reserve_margin": 0.15}}, now=ON + 3600)
        self.assertEqual((reserve, ask, rec["arm"], rec["share"]), (6900, 6900, "D", 0.15))

    def test_default_food_margin_raises_reserve_and_records_default(self):
        u = unit()
        q = self.q(u)
        reserve, ask, rec = self.run_(u, q, pricing={"price": {"food_reserve_margin": 0.10}})
        self.assertEqual((reserve, ask), (32917, 32917))
        self.assertIsNotNone(rec)
        self.assertEqual((rec["study"], rec["arm"], rec["share"], rec["stop_at"]),
                         ("default", "D", 0.10, None))
        self.assertEqual((rec["usual_reserve"], rec["usual_ask"], rec["cost"], rec["unit"]),
                         (30000, 30000, 29924, u["unit"]))

    def test_repository_pricing_enables_default_food_margin(self):
        with Path(fd.__file__).with_name("pricing.json").open(encoding="utf-8") as f:
            pricing = json.load(f)
        u = unit()
        rec = self.run_(u, self.q(u), pricing=pricing)[2]
        self.assertIsNotNone(rec)
        self.assertEqual((rec["arm"], rec["share"]), ("D", 0.15))

    def test_default_fifteen_percent_caps_at_list_and_never_lowers_today(self):
        pricing = {"price": {"food_reserve_margin": 0.15}}
        for cost, floor, opening, expected in ((6000, 6016, 7080, (6900, 7080)),
                                               (9500, 9600, 9700, (10000, 10000)),
                                               (10000, 11000, 12000, (11000, 12000))):
            with self.subTest(cost=cost):
                result = self.run_(unit(cost=cost, list_price=10000),
                                   {"floor": floor, "open": opening}, pricing=pricing)
                self.assertEqual(result[:2], expected)
                self.assertEqual((result[2]["arm"], result[2]["share"]), ("D", 0.15))

    def test_default_caps_at_list_and_never_lowers_today(self):
        pricing = {"price": {"food_reserve_margin": 0.10}}
        for cost, reserve, ask, expected in ((37000, 37100, 37200, (37900, 37900)),
                                             (38000, 39000, 40000, (39000, 40000)),
                                             (10000, 12000, 15000, (12000, 15000))):
            with self.subTest(cost=cost):
                result = fd.decide(unit(cost=cost), {}, reserve=reserve, ask=ask, keep=1,
                                   margin_assignment=None, pricing=pricing, md=self.md.name, env={}, now=ON)
                self.assertEqual(result[:2], expected)
                self.assertEqual(result[2]["arm"], "D")

    def test_ten_percent_margin_has_no_float_extra_won(self):
        u = unit(cost=6000, list_price=10000)
        q = {"floor": 6016, "open": 7080}
        pricing = {"price": {"food_reserve_margin": 0.10}}
        self.assertEqual(self.run_(u, q, pricing=pricing)[:2], (6600, 7080))
        self.on(salt=self.salt_for("B", u), share=0.10)
        self.assertEqual(self.run_(u, q, pricing=pricing)[:2], (6600, 7080))

    def test_default_off_switch_is_read_at_every_quote(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        path = Path(self.md.name, fd.SWITCH)
        self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "D")
        path.write_text(json.dumps({"enabled": False, "default_off": True}))
        self.assertEqual(self.run_(u, q, pricing=pricing), (30000, 30000, None))
        path.write_text(json.dumps({"enabled": False}))
        self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "D")
        path.write_text("unreadable json")
        self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "D")
        self.assertEqual(self.run_(u, q, pricing=pricing, env={"SELLER_FOOD_MARGIN": "0"}),
                         (30000, 30000, None))

    def test_running_study_ignores_default_off_field(self):
        u = unit()
        self.on(salt=self.salt_for("B"), default_off=True)
        rec = self.run_(u, self.q(u), pricing={"price": {"food_reserve_margin": 0.10}})[2]
        self.assertEqual((rec["study"], rec["arm"]), ("food-margin-t", "B"))

    def test_default_off_helper_replaces_study_with_private_stop_file(self):
        self.on()
        expected = {"enabled": False, "default_off": True}
        self.assertEqual(fd.default_off(self.md.name), expected)
        path = Path(self.md.name, fd.SWITCH)
        self.assertEqual(json.loads(path.read_text()), expected)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_default_setting_warns_once_without_raising(self):
        u = unit()
        q = self.q(u)
        stderr = io.StringIO()
        with mock.patch.object(fd, "_warned_invalid_default", False, create=True), mock.patch("sys.stderr", stderr):
            self.run_(u, q, pricing={"price": {}})
            self.assertEqual(stderr.getvalue(), "")
            for bad in (True, 0.1500001, 0.16, "0.10", float("nan")):
                self.assertEqual(self.run_(u, q, pricing={"price": {"food_reserve_margin": bad}}),
                                 (30000, 30000, None))
            self.assertEqual(stderr.getvalue().count("SELLER_FOOD_MARGIN invalid price.food_reserve_margin"), 1)

    def test_unreadable_switch_warns_once_and_keeps_default(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        for error in (PermissionError("fixture"), OSError("fixture"), ValueError("bad JSON")):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                    fd, "_warned_unreadable_switch", False, create=True), mock.patch(
                    "sys.stderr", new_callable=io.StringIO) as stderr:
                with mock.patch.object(Path, "read_text", side_effect=error):
                    for _ in range(2):
                        self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "D")
                self.assertEqual(stderr.getvalue(),
                                 f"SELLER_FOOD_MARGIN switch unreadable ({type(error).__name__}); default applies\n")

    def test_switch_on_refuses_invalid_or_non_dict_json_without_changing_file(self):
        path = Path(self.md.name, fd.SWITCH)
        for contents in (b'invalid JSON', b'[]', b'null', b'"text"', b'42'):
            with self.subTest(contents=contents):
                path.write_bytes(contents)
                before = path.stat()
                with self.assertRaisesRegex(ValueError, r"existing switch unreadable \([A-Za-z]+\); not replaced") as raised:
                    fd.switch_on(self.md.name, study="test", salt="test", start_at=ON, hours=1)
                self.assertNotIn(self.md.name, str(raised.exception))
                after = path.stat()
                self.assertEqual(path.read_bytes(), contents)
                self.assertEqual((after.st_mode, after.st_mtime_ns), (before.st_mode, before.st_mtime_ns))
                self.assertEqual(list(Path(self.md.name).glob(fd.SWITCH + '.tmp-*')), [])

    @unittest.skipIf(os.geteuid() == 0, "root can read mode 000")
    def test_switch_on_refuses_permission_denied_without_changing_file(self):
        fd.default_off(self.md.name)
        path = Path(self.md.name, fd.SWITCH)
        with path.open('rb') as original:
            contents = original.read()
            path.chmod(0)
            try:
                before = path.stat()
                with self.assertRaisesRegex(ValueError, r"existing switch unreadable \(PermissionError\); not replaced"):
                    fd.switch_on(self.md.name, study="test", salt="test", start_at=ON, hours=1)
                after = path.stat()
                self.assertEqual((after.st_ino, after.st_mode, after.st_mtime_ns),
                                 (before.st_ino, before.st_mode, before.st_mtime_ns))
                original.seek(0)
                self.assertEqual(original.read(), contents)
                self.assertEqual(list(Path(self.md.name).glob(fd.SWITCH + '.tmp-*')), [])
            finally:
                path.chmod(0o600)  # restore this temporary fixture for cleanup only

    def test_switch_on_creates_missing_file(self):
        result = fd.switch_on(self.md.name, study="test", salt="test", start_at=ON, hours=1)
        self.assertEqual(json.loads(Path(self.md.name, fd.SWITCH).read_text()), result)

    def test_missing_switch_keeps_default_silently(self):
        with mock.patch.object(fd, "_warned_unreadable_switch", False, create=True), mock.patch(
                "sys.stderr", new_callable=io.StringIO) as stderr:
            rec = self.run_(unit(), self.q(unit()), pricing={"price": {"food_reserve_margin": 0.10}})[2]
            self.assertEqual(rec["arm"], "D")
            self.assertEqual(stderr.getvalue(), "")

    def test_switch_on_preserves_default_stop_after_study_ends(self):
        fd.default_off(self.md.name)
        d = fd.switch_on(self.md.name, study="finite-study", salt=self.salt_for("B"),
                         start_at=ON, hours=1)
        self.assertIs(d.get("default_off"), True)
        self.assertIsNotNone(fd.config(self.md.name, env={}, now=ON))
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        rec = self.run_(u, q, pricing=pricing)[2]
        self.assertEqual((rec["study"], rec["arm"]), ("finite-study", "B"))
        self.assertEqual(self.run_(u, q, pricing=pricing, now=ON + 3600), (30000, 30000, None))

    def test_decide_reads_one_switch_snapshot_per_quote(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        read_text = Path.read_text
        for contents in (None, '{"enabled": false}', '{"default_off": true}', 'bad JSON'):
            with self.subTest(contents=contents):
                path = Path(self.md.name, fd.SWITCH)
                if contents is not None:
                    path.write_text(contents)
                with mock.patch.object(Path, "read_text", autospec=True, side_effect=read_text) as reader:
                    self.run_(u, q, pricing=pricing)
                    self.assertEqual(reader.call_count, 1)
        self.on(salt=self.salt_for("B"))
        with mock.patch.object(Path, "read_text", autospec=True, side_effect=read_text) as reader:
            self.assertEqual(self.run_(u, q, pricing=pricing)[2]["arm"], "B")
            self.assertEqual(reader.call_count, 1)

    def test_default_ineligible_talks_and_env_off_unchanged(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        same = (q["floor"], q["open"], None)
        for changes in ({"keep": 0.9}, {"margin": {}}, {"env": {"SELLER_FOOD_MARGIN": "0"}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.run_(u, q, pricing=pricing, **changes), same)
        for changes in ({"kind": "goods"}, {"list_price": 0}, {"cost": 0}):
            self.assertEqual(self.run_(unit(**changes), q, pricing=pricing), same)
        for why in ("ask cap 30500", "scarce ask 31000", "trial 0.9 x median"):
            self.assertEqual(self.run_(u, {**q, "why": why}, pricing=pricing), same)
        capped = {"price": {**pricing["price"], "ask_caps": {u["sku"]: {"A": 30500}}}}
        self.assertEqual(self.run_(u, q, pricing=capped), same)

    def test_default_missing_or_bad_share_keeps_today(self):
        u = unit()
        q = self.q(u)
        for pricing in (None, {}, {"price": {}}, *({"price": {"food_reserve_margin": bad}}
                        for bad in (0.01, 0.1500001, 0.16, True, float("nan"), "0.10", None))):
            with self.subTest(pricing=pricing):
                self.assertEqual(self.run_(u, q, pricing=pricing), (q["floor"], q["open"], None))

    def test_running_study_wins_over_default(self):
        u = unit()
        q = self.q(u)
        pricing = {"price": {"food_reserve_margin": 0.10}}
        for arm, expected in (("A", 30000), ("B", 31421)):
            self.on(salt=self.salt_for(arm))
            reserve, ask, rec = self.run_(u, q, pricing=pricing)
            self.assertEqual((reserve, ask, rec["study"], rec["arm"], rec["share"]),
                             (expected, expected, "food-margin-t", arm, 0.05))
            self.assertEqual(self.run_(u, q, pricing=pricing, env={"SELLER_FOOD_MARGIN": "0"}),
                             (30000, 30000, None))

    def test_off_is_todays_price(self):
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        self.assertEqual(self.run_(u, q), same)
        self.on(enabled=False)
        self.assertEqual(self.run_(u, q), same)
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_FOOD_MARGIN": "0"}), same)

    def test_arm_b_raises_reserve_and_ask_to_cost_plus_share(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual(reserve, 31421)                    # ceil(29924 x 1.05)
        self.assertEqual(ask, 31421)
        self.assertEqual({k: rec[k] for k in ("study", "arm", "share", "usual_reserve", "usual_ask", "reserve", "ask",
                                               "list", "cost", "sku", "grade", "unit")},
                         {"study": "food-margin-t", "arm": "B", "share": 0.05, "usual_reserve": 30000,
                          "usual_ask": 30000, "reserve": 31421, "ask": 31421, "list": 37900, "cost": 29924,
                          "sku": "G200e39a3", "grade": "A", "unit": "u-e9e1c5b1e5f1"})

    def test_arm_a_keeps_todays_price_and_is_recorded(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("A"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask, rec["arm"], rec["reserve"], rec["ask"]), (30000, 30000, "A", 30000, 30000))

    def test_never_lowers_and_never_over_list(self):
        u = unit(cost=10000)                                # B-like cost: the usual ask is already over cost x 1.05
        q = self.q(u, median=25000)
        self.on(salt=self.salt_for("B"))
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask), (10500, q["open"]))
        u = unit(cost=37000)                                # cost x 1.05 over list: list caps it, never under today's
        q = self.q(u)
        reserve, ask, _ = self.run_(u, q)
        self.assertEqual(reserve, max(q["floor"], 37900))
        self.assertLessEqual(reserve, max(q["floor"], u["list_price"]))
        u = unit(cost=38000, list_price=37900.0)            # today's reserve already over a float list: kept as it is
        q = self.q(u)
        self.assertGreater(q["floor"], 37900)
        reserve, ask, rec = self.run_(u, q)
        self.assertEqual((reserve, ask, rec["arm"]), (q["floor"], q["open"], "B"))
        self.assertIs(type(reserve), int)

    def test_ineligible_talks_untouched(self):
        self.on(salt=self.salt_for("B"))
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        self.assertEqual(self.run_(u, q, keep=0.8), same)                   # markdown: near its date, clearance
        self.assertEqual(self.run_(u, q, margin={"arm": "x"}), same)         # margin study talk
        self.assertEqual(self.run_(unit(kind="goods"), q), same)
        self.assertEqual(self.run_(unit(list_price=None), q), same)
        self.assertEqual(self.run_(unit(cost=0), q), same)
        self.assertEqual(self.run_(u, {**q, "why": "scarce ask 31000"}), same)
        self.assertEqual(self.run_(u, {**q, "why": "ask cap 30500 for grade A (was 31000)"}), same)
        self.assertEqual(self.run_(u, {**q, "why": "trial 0.9 x median 29000 (was 31000, x)"}), same)
        capped = {"price": {**PRICING["price"], "ask_caps": {"G200e39a3": {"A": 30500}}}}
        self.assertEqual(self.run_(u, q, pricing=capped), same)

    def test_seller_guards_hold_the_raised_reserve(self):
        import seller
        deal = {"reserve": 31421, "ask": 31421, "open": 31421, "rounds": 1, "kind": "food",
                "history": [("you asked", 31421), ("buyer offered", 30000)]}
        self.assertEqual(seller.price_guards(deal, "accept", 30000, 30000, 31421), ("counter", 31421))

    def test_arm_is_per_unit_and_about_half(self):
        cfg = {"salt": "x"}
        self.assertEqual(fd.arm(cfg, "u1"), fd.arm(cfg, "u1"))
        b = sum(fd.arm(cfg, f"u-{i:012x}") == "B" for i in range(2000))
        self.assertTrue(900 < b < 1100, b)
        self.assertIsNone(fd.arm(cfg, ""))
        self.assertIsNone(fd.arm(None, "u1"))

    def test_bad_settings_are_off(self):
        u = unit()
        q = self.q(u)
        same = (q["floor"], q["open"], None)
        for bad in (0.01, 0.2, True, "0.05", float("nan"), None):
            self.on(salt=self.salt_for("B"), share=bad)
            self.assertEqual(self.run_(u, q), same, bad)
        for k, v in (("study", ""), ("salt", None), ("start_at", None), ("stop_at", float("inf")),
                     ("stop_at", ON - 60), ("stop_at", ON - 60 + fd.MAX_WINDOW_HOURS * 3600 + 1)):
            self.on(**{"salt": self.salt_for("B"), k: v})
            self.assertEqual(self.run_(u, q), same, (k, v))
        Path(self.md.name, fd.SWITCH).write_text('{"enabled": true, "start_at": NaN')
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
        with mock.patch.object(fd.profit_price, "ask_cap", side_effect=RuntimeError("x")):
            self.assertEqual(self.run_(u, q), (q["floor"], q["open"], None))
        self.assertEqual(fd.decide(None, {}, reserve=4, ask=5, keep=1, margin_assignment=None, md=self.md.name,
                                   env={}, now=ON), (4, 5, None))
        self.assertEqual(fd.decide(unit(), None, reserve=4, ask=5, keep=1, margin_assignment=None, md=self.md.name,
                                   env={}, now=ON), (4, 5, None))

    def test_producer_writes_a_finite_window(self):
        d = fd.switch_on(self.md.name, study="food-margin-t", salt="s1", start_at=ON, hours=12)
        self.assertEqual((d["start_at"], d["stop_at"], d["share"]), (ON, ON + 12 * 3600, fd.SHARE))
        self.assertIsNotNone(fd.config(self.md.name, env={}, now=ON + 1))
        self.assertIsNone(fd.config(self.md.name, env={}, now=ON + 12 * 3600))
        self.assertEqual(os.stat(os.path.join(self.md.name, fd.SWITCH)).st_mode & 0o777, 0o600)
        for hours in (0, -1, fd.MAX_WINDOW_HOURS + 0.01, float("nan"), True):
            with self.assertRaises(ValueError):
                fd.switch_on(self.md.name, study="food-margin-t", salt="s1", start_at=ON, hours=hours)
        self.assertIsNotNone(fd.config(self.md.name, env={}, now=ON + 1))

    def test_seller_calls_it_after_floor_margin_and_records_it(self):
        src = Path(__file__).with_name("seller.py").read_text(encoding="utf-8")
        f, d = src.index("seller_floor_margin.decide("), src.index("seller_food_margin.decide(")
        self.assertLess(f, d)
        self.assertLess(d, src.index('deal = self.open[task.id] = {"id": u["sku"]'))
        self.assertIn('**({"food_margin": deal["food_margin"]} if deal.get("food_margin") else {})', src)

    def test_meta_harness_watches_the_switch(self):
        import meta_harness
        self.assertIn(".market/" + fd.SWITCH, meta_harness.SWITCHES)


if __name__ == "__main__":
    unittest.main()
