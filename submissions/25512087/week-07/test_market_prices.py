"""Tests for the market price scan and the pricing cap it drives."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from market_prices import detect_surge, shares, step, summarize
from pricing import AdaptivePricer

RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def trades(spec: list[tuple[str, str, int]]) -> list[dict]:
    return [{"shop": s, "item": i, "price": p, "counted": True} for s, i, p in spec] + \
           [{"shop": "x", "item": None, "price": None, "counted": False}]


class ScanTests(unittest.TestCase):
    def test_summary_uses_counted_deals_only(self) -> None:
        s = summarize(trades([("a", "lamp", 100), ("b", "lamp", 110), ("c", "lamp", 130)]))
        self.assertEqual(s["items"]["lamp"]["min"], 100)
        self.assertEqual(s["items"]["lamp"]["median"], 110)
        self.assertEqual(s["window_deals"], 3)

    def test_surge_needs_a_real_jump(self) -> None:
        before = shares(trades([("a", "lamp", 100)] * 2 + [("b", "lamp", 110)] * 18))
        after = shares(trades([("a", "lamp", 100)] * 8 + [("b", "lamp", 110)] * 12))
        surges = detect_surge(before, after, "me")
        self.assertEqual([s["shop"] for s in surges], ["a"])
        self.assertEqual(detect_surge(after, after, "me"), [])
        self.assertEqual(detect_surge(before, shares(trades([("me", "lamp", 100)] * 9 + [("b", "lamp", 1)])), "me"), [])

    def test_step_scans_on_schedule_and_on_surge(self) -> None:
        with TemporaryDirectory() as d:
            kw = dict(state_path=Path(d) / "st.json", prices_path=Path(d) / "p.json", log_path=Path(d) / "l.jsonl")
            calm = trades([("a", "lamp", 100)] * 2 + [("b", "lamp", 110)] * 18)
            t0 = 1_000_000.0
            self.assertIn("scheduled", step(calm, "me", 1.0, now=t0, **kw)["reason"])     # first poll
            self.assertIsNone(step(calm, "me", 1.0, now=t0 + 600, **kw))                  # nothing new
            surge = trades([("a", "lamp", 95)] * 8 + [("b", "lamp", 110)] * 12)
            scan = step(surge, "me", 1.0, now=t0 + 1200, **kw)
            self.assertIn("share surge", scan["reason"])
            self.assertEqual(scan["leaders"]["a"]["lamp"], 95)
            self.assertIsNone(step(surge, "me", 1.0, now=t0 + 1800, **kw))
            self.assertIn("scheduled", step(surge, "me", 1.0, now=t0 + 1200 + 3600, **kw)["reason"])
            self.assertEqual(len((Path(d) / "l.jsonl").read_text().splitlines()), 3)


class MarketCapTests(unittest.TestCase):
    def _pricer(self, d: str, scan: dict | None) -> AdaptivePricer:
        mp = Path(d) / "prices.json"
        if scan is not None:
            mp.write_text(json.dumps(scan))
        return AdaptivePricer(RESERVES, Path(d) / "s.json", Path(d) / "e.jsonl", Path(d) / "launch.json", mp)

    def test_ask_never_above_market_upper_quartile(self) -> None:
        with TemporaryDirectory() as d:
            self.assertEqual(self._pricer(d, None).ask("chair"), 195)          # never measured: 180 * 1.08
        with TemporaryDirectory() as d:
            q = {"ts": time.time(), "items": {"chair": {"median": 180, "p75": 184}}, "leaders": {"z": {"chair": 180}}}
            self.assertEqual(self._pricer(d, q).ask("chair"), 184)          # p75, not the surging shop's median
        with TemporaryDirectory() as d:
            old = {"ts": time.time(), "items": {"chair": {"median": 186}}, "leaders": {}}
            self.assertEqual(self._pricer(d, old).ask("chair"), 186)        # scan without p75: median
        with TemporaryDirectory() as d:
            floor = {"ts": time.time(), "items": {"chair": {"median": 170, "p75": 175}}, "leaders": {}}
            self.assertEqual(self._pricer(d, floor).ask("chair"), 180)      # never below reserve

    def test_high_market_capped_stale_scan_keeps_last_p75(self) -> None:
        with TemporaryDirectory() as d:
            high = {"ts": time.time(), "items": {"lamp": {"median": 130, "p75": 140}}, "leaders": {}}
            self.assertEqual(self._pricer(d, high).ask("lamp"), 110)        # learned 1.15, capped at 1.10
        with TemporaryDirectory() as d:
            stale = {"ts": time.time() - 4 * 3600, "items": {"lamp": {"median": 100, "p75": 103}}, "leaders": {}}
            self.assertEqual(self._pricer(d, stale).ask("lamp"), 103)


class LowestPriceBuyerTests(unittest.TestCase):
    def test_ladder_opens_at_floor_and_walks_early_below_floor(self) -> None:
        from negotiation import LadderBuyerPolicy
        p = LadderBuyerPolicy()
        self.assertEqual(p.ladder("chair", 250), [180, 186, 193, 222, 250])
        self.assertEqual(p.ladder("lamp", 102), [100, 102])
        self.assertEqual(p.ladder("bookshelf", 230), [230])
        q = LadderBuyerPolicy()
        self.assertEqual(q.respond("bookshelf", 230, 338, 0).price, 230)
        self.assertEqual(q.respond("bookshelf", 230, 300, 1).action, "walk")
        r = LadderBuyerPolicy()
        self.assertEqual(r.respond("lamp", 140, 130, 0).price, 100)
        self.assertEqual(r.respond("lamp", 140, 102, 1).price, 102)        # ask under next rung: take it

    def test_cheaper_shop_wins_when_otherwise_equal(self) -> None:
        from market_buyer import choose_shop
        from market_prices import shop_prices
        no_explore = type("NoExplore", (), {"random": lambda self: 1.0})()
        prices = shop_prices(trades([("cheap", "chair", 180)] * 3 + [("dear", "chair", 200)] * 3))
        shops = [{"name": "dear", "url": "1", "reviews": []}, {"name": "cheap", "url": "2", "reviews": []}]
        self.assertEqual(choose_shop(shops, [], "chair", rng=no_explore, prices=prices)[0]["name"], "cheap")


if __name__ == "__main__":
    unittest.main()
