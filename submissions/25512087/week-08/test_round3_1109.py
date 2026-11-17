"""11/09 approved changes: real-talk signing rate (measure/tune) and seller unit choice (refused units, hours asked)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "improve"))
import tuning  # noqa: E402

tuning.POLICY_FILE = Path(tempfile.gettempdir()) / "no-such-policy-1109.json"
import seller_r3  # noqa: E402
import measure  # noqa: E402
import tune  # noqa: E402


def sl(ev):
    return "00:00:00 [seller] " + json.dumps(ev)


class RealTalks(unittest.TestCase):
    def log(self):
        lines = []
        for i in range(12):                                   # 12 walk-aways on one SKU: repeat probing
            lines.append(sl({"event": "opening", "task": f"w{i}", "sku": "GREP", "unit": "u-1", "cost": 50, "ask": 120, "floor": 90}))
        lines.append(sl({"event": "opening", "task": "w-other", "sku": "GONE", "unit": "u-2", "cost": 50, "ask": 120, "floor": 90}))
        for i in range(3):                                    # lowball probes
            lines.append(sl({"event": "opening", "task": f"l{i}", "sku": "GX", "unit": "u-3", "cost": 50, "ask": 120, "floor": 90}))
            lines.append(sl({"event": "decision", "task": f"l{i}", "offer": 6, "action": "counter", "price": 90}))
        for i in range(4):                                    # signed
            lines.append(sl({"event": "opening", "task": f"s{i}", "sku": "GX", "unit": f"u-s{i}", "cost": 50, "ask": 120, "floor": 90}))
            lines.append(sl({"event": "decision", "task": f"s{i}", "offer": 100, "action": "accept", "price": 100}))
        lines.append(sl({"event": "opening", "task": "n1", "sku": "GX", "unit": "u-9", "cost": 50, "ask": 120, "floor": 90}))
        lines.append(sl({"event": "decision", "task": "n1", "offer": 85, "action": "counter", "price": 90}))
        return "\n".join(lines)

    def test_counts(self):
        m = measure.parse_text(self.log())
        self.assertEqual(m["openings"], 21)
        self.assertEqual(m["repeat_walks"], 12)
        self.assertEqual(m["lowball_talks"], 3)
        self.assertEqual(m["real_talks"], 6)              # 1 lone walk-away + 4 signed + 1 near miss
        self.assertAlmostEqual(m["real_accept_rate"], round(4 / 6, 3))
        self.assertAlmostEqual(m["accept_rate"], round(4 / 21, 3))

    def test_merge(self):
        a = measure.parse_text(self.log())
        out = measure.merge(a, a)
        self.assertEqual(out["real_talks"], 12)
        self.assertAlmostEqual(out["real_accept_rate"], round(8 / 12, 3))

    def pol(self):
        return {k: dict(v) for k, v in tune.DEFAULTS.items()}

    def m(self, shop):
        return {"window": {"shop": shop, "buyer": {}}, "shop_now": {}, "window_hours": 3}

    def test_tune_uses_real_rate(self):
        shop = {"openings": 600, "accept_rate": 0.28, "real_talks": 220, "real_accept_rate": 0.72}
        ch = [c for c in tune.propose(self.m(shop), self.pol()) if c["key"] == "ask_of_list"]
        self.assertEqual(ch, [])                           # 72 % of real talks: no cut

    def test_tune_cuts_on_low_real_rate(self):
        shop = {"openings": 600, "accept_rate": 0.28, "real_talks": 220, "real_accept_rate": 0.30}
        ch = [c for c in tune.propose(self.m(shop), self.pol()) if c["key"] == "ask_of_list"]
        self.assertEqual(len(ch), 1)
        self.assertLess(ch[0]["new"], ch[0]["old"])

    def test_tune_old_metrics_fall_back(self):
        shop = {"openings": 600, "accept_rate": 0.28}
        ch = [c for c in tune.propose(self.m(shop), self.pol()) if c["key"] == "ask_of_list"]
        self.assertEqual(len(ch), 1)


class SellerUnitChoice(unittest.TestCase):
    def inv(self, now):
        mk = lambda u, h: {"unit": u, "sku": "G74e686be", "name": "tangerine 9kg", "kind": "food", "grade": "B",
                           "list_price": 13500, "cost": 5886, "supplied_at": 1, "expires_at": now + h * 3600}
        return {"round": 3, "units": [mk("u-old", 25.7), mk("u-mid", 29.9), mk("u-new", 33.5)]}

    def test_default_first_expiring(self):
        now = time.time()
        self.assertEqual(seller_r3.find_unit("I want G74e686be", self.inv(now), now=now)["unit"], "u-old")

    def test_hours_asked(self):
        now = time.time()
        self.assertEqual(seller_r3.hours_asked("must last 28 h"), 28)
        self.assertEqual(seller_r3.hours_asked("최소 30시간"), 30)
        self.assertIsNone(seller_r3.hours_asked("I want G74e686be"))
        u = seller_r3.find_unit("G74e686be, it must last 28 hours", self.inv(now), now=now)
        self.assertEqual(u["unit"], "u-mid")
        u = seller_r3.find_unit("G74e686be, at least 40 h left", self.inv(now), now=now)
        self.assertEqual(u["unit"], "u-new")                # none has 40 h: the one with most left

    def test_refused_skipped(self):
        now = time.time()
        u = seller_r3.find_unit("I want G74e686be", self.inv(now), refused={"u-old"}, now=now)
        self.assertEqual(u["unit"], "u-mid")
        self.assertIsNone(seller_r3.find_unit("I want G74e686be", self.inv(now), refused={"u-old", "u-mid", "u-new"}, now=now))

    def test_refused_units_from_log(self):
        now = time.mktime(time.strptime("2026-11-09 12:30:00", "%Y-%m-%d %H:%M:%S"))
        lines = ["12:00:01 [acp] checkout refused: unit u-aaa is not in stock for this order",
                 "12:10:01 [acp] checkout refused: 409 {\"error\":\"unit u-aaa is not in stock for this order\"}",
                 "12:20:01 [acp] checkout refused: unit u-bbb is not in stock for this order",
                 "10:00:01 [acp] checkout refused: unit u-ccc is not in stock for this order",
                 "10:05:01 [acp] checkout refused: unit u-ccc is not in stock for this order",
                 "12:21:01 [seller] {\"event\": \"opening\"}"]
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("\n".join(lines))
        got = seller_r3.refused_units(now=now, path=Path(f.name))
        self.assertEqual(got, frozenset({"u-aaa"}))           # u-bbb once, u-ccc too old

    def test_refused_units_midnight(self):
        now = time.mktime(time.strptime("2026-11-09 00:10:00", "%Y-%m-%d %H:%M:%S"))
        lines = ["23:50:00 [acp] checkout refused: unit u-ffe is not in stock for this order",
                 "00:05:00 [acp] checkout refused: unit u-ffe is not in stock for this order"]
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as f:
            f.write("\n".join(lines))
        self.assertEqual(seller_r3.refused_units(now=now, path=Path(f.name)), frozenset({"u-ffe"}))


if __name__ == "__main__":
    unittest.main()
