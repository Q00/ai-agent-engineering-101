"""Unit tests for ops10 stop/gate logic on synthetic state (no network, no files)."""
import unittest
import ops10 as o

H = 3600
T0 = 1_000_000_000  # synthetic epoch
BASE = {"refunds": 0, "broken": 0}


def st(windows=(), settles=(), results=(), incidents=(), start=T0):
    return {"start": start, "base": dict(BASE), "windows": list(windows), "settles": list(settles),
            "results": list(results), "incidents": list(incidents)}


def win(*units):  # units: (unit_id, cost, supplied_ts)
    return {"purchases": [{"sku": "S", "qty": 1, "unit_cost": c, "units": [u], "supplied_ts": ts} for u, c, ts in units]}


def bm(stars=4.05, refunds=0, broken=0):
    return {"stars_rank": stars, "refunds": refunds, "broken": broken}


def settle(unit, amount, ts):
    return {"unit": unit, "amount": amount, "ts": ts, "seq": ts, "sku": "S", "buyer": "b"}


class StopTests(unittest.TestCase):
    def test_no_premature_stop_right_after_purchase(self):
        # last settle long ago (T0-2h19m), unit bought 4 min before check -> must NOT stop
        s = st([win(("u1", 12645, T0 + 2 * H + 15 * 60))], [settle("old", 9000, T0 - 2 * H)], start=T0 + 2 * H)
        r = o.stop_reasons(s, bm(), {"u1"}, T0 + 2 * H + 19 * 60)
        self.assertEqual(r, [])

    def test_real_two_hours_without_settlement_stops(self):
        s = st([win(("u1", 12645, T0))], [settle("old", 9000, T0 - 3 * H)], start=T0 - 10 * 60)
        r = o.stop_reasons(s, bm(), {"u1"}, T0 + 2 * H + 1)
        self.assertTrue(any("no settlement for 2 h" in x for x in r))

    def test_settlement_resets_clock(self):
        s = st([win(("u1", 10000, T0), ("u2", 10000, T0))], [settle("u2", 11500, T0 + 1 * H)], start=T0)
        self.assertEqual(o.stop_reasons(s, bm(), {"u1"}, T0 + 2 * H + 30 * 60), [])  # 1h30 since settle
        r = o.stop_reasons(s, bm(), {"u1"}, T0 + 3 * H + 1)
        self.assertTrue(any("no settlement for 2 h" in x for x in r))

    def test_staggered_units_partial_sale(self):
        # u1 in at T0 sold at T0+30m; u2 in at T0+1h unsold; clock = max(T0+30m, T0+1h) = T0+1h
        s = st([win(("u1", 9000, T0)), win(("u2", 9500, T0 + H))], [settle("u1", 11000, T0 + 30 * 60)], start=T0)
        self.assertEqual(o.stop_reasons(s, bm(), {"u2"}, T0 + 2 * H + 50 * 60), [])  # 1h50 after u2
        r = o.stop_reasons(s, bm(), {"u2"}, T0 + 3 * H + 1)
        self.assertTrue(any("no settlement for 2 h" in x for x in r))

    def test_nothing_unsold_never_triggers_time_rules(self):
        s = st([win(("u1", 9000, T0))], [settle("u1", 11000, T0 + 10 * 60)], start=T0)
        self.assertEqual(o.stop_reasons(s, bm(), set(), T0 + 9 * H), [])

    def test_checkpoint_zero_settlements(self):
        s = st([win(("u1", 9000, T0 + 100 * 60))], [settle("old", 9000, T0 + 110 * 60)], start=T0)
        r = o.stop_reasons(s, bm(), {"u1"}, T0 + 2 * H + 1)
        self.assertTrue(any("checkpoint" in x for x in r))
        self.assertFalse(any("no settlement for 2 h" in x for x in r))

    def test_checkpoint_passes_with_one_run_settlement(self):
        s = st([win(("u1", 9000, T0)), win(("u2", 9000, T0 + 110 * 60))], [settle("u1", 10500, T0 + 90 * 60)], start=T0)
        self.assertEqual(o.stop_reasons(s, bm(), {"u2"}, T0 + 2 * H + 1), [])

    def test_first_purchase_after_checkpoint_not_stopped(self):
        # nothing bought in the first 2 h; first unit at T0+3h must not be stopped by the checkpoint
        s = st([win(("u1", 9000, T0 + 3 * H))], [settle("old", 9000, T0 + 2 * H + 50 * 60)], start=T0)
        self.assertEqual(o.stop_reasons(s, bm(), {"u1"}, T0 + 3 * H + 5 * 60), [])

    def test_loss_refund_dup_reputation(self):
        s = st([win(("u1", 12000, T0))], [settle("u1", 11000, T0 + 600)], start=T0)
        self.assertIn("settled below cost", o.stop_reasons(s, bm(), set(), T0 + 700))
        s = st(start=T0); self.assertIn("refund/broken increased", o.stop_reasons(s, bm(refunds=1), set(), T0))
        s = st([win(("u1", 9000, T0))], [settle("u1", 10000, T0 + 1), settle("u1", 10000, T0 + 2)], start=T0)
        self.assertTrue(any("duplicate settle" in x for x in o.stop_reasons(s, bm(), set(), T0 + 3)))
        s = st(start=T0); self.assertTrue(any("stars_rank" in x for x in o.stop_reasons(s, bm(stars=3.92), set(), T0)))
        res = [{"buyer": "a", "stars": 1, "verified": True}, {"buyer": "b", "stars": 2, "verified": True}]
        self.assertTrue(any("verified low-star" in x for x in o.stop_reasons(st(results=res), bm(), set(), T0)))
        res = [{"buyer": "a", "stars": 1, "verified": False}, {"buyer": "b", "stars": 1, "verified": False}]
        self.assertEqual(o.stop_reasons(st(results=res), bm(), set(), T0), [])  # unpaid reviews are not a stop
        self.assertIn("incident expire", o.stop_reasons(st(incidents=[{"kind": "expire"}]), bm(), set(), T0))


class GateStatsTests(unittest.TestCase):
    def mkt(self, n, sold_frac, cost, price, now):
        sup, sett = {}, {}
        for i in range(n):
            ts = now - 4 * H
            sup[f"c{i}"] = ["S", cost, ts]
            if i < n * sold_frac: sett[f"c{i}"] = [price, ts + 20 * 60]
        return {"sup": sup, "set": sett, "settle_ts": {"S": [now - 60] * 30}}

    def test_right_censoring_excluded(self):
        now = T0
        m = self.mkt(20, 0.8, 9000, 10500, now)
        for i in range(50):  # fresh units (<3 h old, unsold) must not dilute the rate
            m["sup"][f"f{i}"] = ["S", 9000, now - H]
        info, why = o.sku_stats("S", m, now)
        self.assertIsNone(why); self.assertEqual(info["cohort"], 20); self.assertAlmostEqual(info["sell_rate_3h"], 0.8)

    def test_rejects_low_rate_low_profit_high_loss(self):
        self.assertIsNotNone(o.sku_stats("S", self.mkt(20, 0.5, 9000, 10500, T0), T0)[1])
        self.assertIsNotNone(o.sku_stats("S", self.mkt(20, 0.9, 9000, 9500, T0), T0)[1])
        m = self.mkt(20, 0.9, 9000, 10500, T0)
        for i in range(5): m["set"][f"c{i}"][0] = 8000  # 5/18 below cost
        self.assertIn("below-cost", o.sku_stats("S", m, T0)[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
