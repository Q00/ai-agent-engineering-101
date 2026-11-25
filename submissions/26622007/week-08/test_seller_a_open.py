import json
import os
import tempfile
import time
import unittest

import profit_price
import seller_a_open as ao


def unit(**kw):
    return {"sku": "G24f5f979", "unit": "u1", "kind": "goods", "grade": "A", "cost": 1696000, "list_price": 2630000,
            **kw}


class SellerAOpenTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def on(self, **kw):
        now = time.time()
        with open(os.path.join(self.md.name, ao.SWITCH), "w") as f:
            json.dump({"enabled": True, "study": "a-open-t", "salt": "s1", "start_at": now - 60, "stop_at": now + 3600,
                       **kw}, f)

    def salt_for(self, want, sku="G24f5f979", grade="A"):
        for i in range(200):
            if ao.arm({"salt": f"s{i}"}, sku, grade) == want:
                return f"s{i}"
        raise AssertionError

    def run_(self, u, q, keep=1, margin=None, env=None, pricing=None):
        return ao.decide(u, q, keep=keep, margin_assignment=margin, pricing=pricing, md=self.md.name, env=env or {})

    def q(self, u, median=1780000):
        return profit_price.quote(u, median=median, measured=0.03, pricing={"price": {"goods_break": 0.03, "high_median_up": {
            "min_list": 300000, "factor": 0.97}}})

    def test_off_is_todays_ask(self):
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q), (q["open"], None))
        self.on(enabled=False)
        self.assertEqual(self.run_(u, q), (q["open"], None))

    def test_env_off_and_bad_ratio_are_off(self):
        u = unit()
        q = self.q(u)
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(u, q, env={"SELLER_A_OPEN": "0"}), (q["open"], None))
        for bad in (0.6, 0.9, True, "0.76", float("nan")):
            self.on(salt=self.salt_for("B"), ratio=bad)
            self.assertEqual(self.run_(u, q), (q["open"], None), bad)

    def test_arm_b_opens_at_the_list_share_arm_a_keeps_it(self):
        u = unit()
        q = self.q(u)
        self.assertLess(q["open"], 0.76 * u["list_price"])          # today's ask, the weakness
        self.on(salt=self.salt_for("B"))
        p, rec = self.run_(u, q)
        self.assertEqual(p, -(-76 * u["list_price"] // 100))
        self.assertIs(type(p), int)
        self.assertEqual((rec["arm"], rec["usual"], rec["open"]), ("B", q["open"], p))
        self.on(salt=self.salt_for("A"))
        p, rec = self.run_(u, q)
        self.assertEqual((p, rec["arm"], rec["open"]), (q["open"], "A", q["open"]))

    def test_never_lowers_and_never_over_list(self):
        u = unit()
        self.on(salt=self.salt_for("B"))
        q = self.q(u, median=2500000)                    # usual ask above 0.76 x list stays
        self.assertEqual(self.run_(u, q)[0], q["open"])
        self.on(salt=self.salt_for("B"), ratio=0.8)
        q = dict(self.q(u), floor=2700000, open=2700000)  # a floor above list stands (F wins)
        self.assertEqual(self.run_(u, q)[0], 2700000)
        q = dict(self.q(u), open=1000)
        self.assertEqual(self.run_(u, q)[0], -(-80 * u["list_price"] // 100))
        self.assertLess(self.run_(u, q)[0], u["list_price"])

    def test_ineligible_talks_untouched(self):
        self.on(salt=self.salt_for("B"))
        for u in (unit(grade="B"), unit(grade="C"), unit(kind="food"), unit(list_price=299000, cost=190000),
                  unit(list_price=None)):
            q = self.q(u) if u.get("list_price") else {"open": 100, "floor": 90, "why": ""}
            self.assertEqual(self.run_(u, q), (q["open"], None), u)
        u = unit()
        q = self.q(u)
        self.assertEqual(self.run_(u, q, keep=0.9), (q["open"], None))
        self.assertEqual(self.run_(u, q, margin={"arm": "A"}), (q["open"], None))
        for caps in ({"G24f5f979": {"A": 9000000}}, {"G24f5f979": {"*": 9000000}}):   # a cap that does not bind
            self.assertEqual(self.run_(u, q, pricing={"price": {"ask_caps": caps}}), (q["open"], None), caps)
        self.assertEqual(self.run_(u, q, pricing={"price": {"ask_caps": {"G24f5f979": {"B": 1}}}})[1]["arm"], "B")
        for why in ("ask cap 1800000 for grade A (was ...)", "scarce ask 1900000", "trial 0.9 x median 1"):
            self.assertEqual(self.run_(u, dict(q, why=why)), (q["open"], None), why)

    def test_arm_is_per_sku_grade(self):
        cfg = {"salt": "x"}
        self.assertEqual(ao.arm(cfg, "G1", "A"), ao.arm(cfg, "G1", "A"))
        arms = {ao.arm(cfg, f"G{i}", "A") for i in range(40)}
        self.assertEqual(arms, {"A", "B"})

    def test_never_raises(self):
        self.on(salt=self.salt_for("B"))
        self.assertEqual(self.run_(unit(), {"open": 5, "floor": None}), (5, None))
        self.assertEqual(ao.decide(None, {"open": 5}, keep=1, margin_assignment=None, md=self.md.name, env={})[0], 5)


# 11/09 18:12 incident (parent review of 742a788): config() read enabled/ratio only and the ON script wrote no times, so
# the B treatment would have run past its planned 9 h; the registry's expiry stops nothing. The real switch as written:
REAL_1812 = {"enabled": True, "study": "a-open-1033", "salt": "a-open-1033-4016", "ratio": 0.76}
ON_1812 = 1794215549.1207738          # 18:12:29 KST; planned sales stop ON+9h = 03:12:29


class SellerAOpenWindowTest(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.TemporaryDirectory()
        self.addCleanup(self.md.cleanup)

    def write(self, d):
        with open(os.path.join(self.md.name, ao.SWITCH), "w") as f:
            f.write(d if isinstance(d, str) else json.dumps(d))

    def cfg(self, now):
        return ao.config(self.md.name, env={}, now=now)

    def test_real_1812_switch_without_times_is_off(self):
        self.write(REAL_1812)
        for now in (ON_1812, ON_1812 + 9 * 3600 + 1, ON_1812 + 30 * 3600):
            self.assertIsNone(self.cfg(now), now)

    def test_finite_window_boundaries(self):
        stop = ON_1812 + 9 * 3600
        self.write(dict(REAL_1812, start_at=ON_1812, stop_at=stop))
        self.assertIsNone(self.cfg(ON_1812 - 0.001))               # not started yet (future start)
        self.assertEqual(self.cfg(ON_1812)["study"], "a-open-1033")
        self.assertIsNotNone(self.cfg(stop - 0.001))
        self.assertIsNone(self.cfg(stop))                           # [start, stop): off at the boundary
        self.assertIsNone(self.cfg(stop + 0.001))

    def test_missing_or_bad_times_are_off(self):
        good = dict(REAL_1812, start_at=ON_1812, stop_at=ON_1812 + 3600)
        bads = [{k: v for k, v in good.items() if k != "start_at"}, {k: v for k, v in good.items() if k != "stop_at"}]
        for k in ("start_at", "stop_at"):
            for v in (None, True, "1794215549", float("nan"), float("inf"), -1, 0, [1]):
                bads.append(dict(good, **{k: v}))
        bads += [dict(good, stop_at=ON_1812), dict(good, stop_at=ON_1812 - 1),            # empty or reversed
                 dict(good, stop_at=ON_1812 + ao.MAX_WINDOW_HOURS * 3600 + 1)]            # longer than the cap
        for b in bads:
            self.write(b)
            self.assertIsNone(self.cfg(ON_1812 + 10), b)
        self.write('{"enabled": true, "start_at": NaN')
        self.assertIsNone(self.cfg(ON_1812 + 10))

    def test_decide_uses_the_clock(self):
        stop = ON_1812 + 9 * 3600
        u = {"sku": "G0667d440", "unit": "u1", "kind": "goods", "grade": "A", "cost": 246497, "list_price": 347850}
        q = {"open": 256077, "floor": 256077, "why": "0.97 x median 238520"}
        salt = next(f"s{i}" for i in range(200) if ao.arm({"salt": f"s{i}"}, u["sku"], "A") == "B")
        self.write(dict(REAL_1812, salt=salt, start_at=ON_1812, stop_at=stop))
        p, rec = ao.decide(u, q, keep=1, margin_assignment=None, md=self.md.name, env={}, now=ON_1812 + 60)
        self.assertEqual((p, rec["arm"]), (264366, "B"))           # prod 18:16:38 A_OPEN G0667d440 first ask 264366
        self.assertEqual(rec["stop_at"], stop)
        self.assertEqual(ao.decide(u, q, keep=1, margin_assignment=None, md=self.md.name, env={}, now=stop),
                         (256077, None))

    def test_producer_writes_a_finite_window_that_config_reads(self):
        d = ao.switch_on(self.md.name, study="a-open-t", salt="s1", start_at=ON_1812, hours=9)
        self.assertEqual((d["start_at"], d["stop_at"]), (ON_1812, ON_1812 + 9 * 3600))
        self.assertIsNotNone(self.cfg(ON_1812 + 1))
        self.assertIsNone(self.cfg(ON_1812 + 9 * 3600))
        self.assertEqual(os.stat(os.path.join(self.md.name, ao.SWITCH)).st_mode & 0o777, 0o600)
        for hours in (0, -1, ao.MAX_WINDOW_HOURS + 0.01, float("nan"), True):
            with self.assertRaises(ValueError):
                ao.switch_on(self.md.name, study="a-open-t", salt="s1", start_at=ON_1812, hours=hours)
        self.assertIsNotNone(self.cfg(ON_1812 + 1))                 # a refused call left the 9 h switch as it was


if __name__ == "__main__":
    unittest.main()
