"""Week 07 tests for review-aware shop selection and durable buyer memory."""

from __future__ import annotations

import json
import unittest
from unittest import mock
from pathlib import Path
from tempfile import TemporaryDirectory

from market_buyer import MemoryStore, choose_shop


class MarketBuyerTests(unittest.TestCase):
    def test_prefers_honest_reviews_when_shops_otherwise_match(self) -> None:
        shops = [
            {
                "name": "bad-shop",
                "url": "http://bad.test",
                "reviews": [
                    {"stars": 1, "review": "Receipt price was higher than agreed."},
                    {"stars": 2, "review": "Overcharged on the receipt, avoid."},
                ],
            },
            {
                "name": "good-shop",
                "url": "http://good.test",
                "reviews": [
                    {"stars": 5, "review": "Fair price, receipt matched."},
                    {"stars": 4, "review": "Quick deal at a fair price."},
                ],
            },
        ]
        chosen, reason = choose_shop(shops, [])
        self.assertEqual(chosen["name"], "good-shop")
        self.assertIn("review", reason.lower())

    def test_successful_memory_breaks_tie_between_unreviewed_shops(self) -> None:
        shops = [
            {"name": "new-shop", "url": "http://new.test", "reviews": []},
            {"name": "known-shop", "url": "http://known.test", "reviews": []},
        ]
        memories = [
            {"shop": "known-shop", "item": "lamp", "price": 105, "outcome": "deal"}
        ]
        no_explore = type("NoExplore", (), {"random": lambda self: 1.0})()   # exploration off
        chosen, _ = choose_shop(shops, memories, rng=no_explore)
        self.assertEqual(chosen["name"], "known-shop")

    def test_memory_store_appends_json_lines(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.jsonl"
            store = MemoryStore(path)
            record = {"shop": "good-shop", "item": "lamp", "price": 105, "outcome": "deal"}
            store.append(record)
            self.assertEqual(store.load(), [record])
            self.assertEqual(json.loads(path.read_text().strip()), record)


if __name__ == "__main__":
    unittest.main()


class FallbackTests(unittest.TestCase):
    """10/26: an order lost to a shop that did not answer is retried once at the next best shop."""

    def _run(self, outcomes):
        import contextlib, io, json as _json, sys as _sys
        from tempfile import TemporaryDirectory
        from pathlib import Path as _P
        import market_buyer as mb
        calls = []

        def fake_trade(chosen, args, memories, memory):
            calls.append(chosen["name"])
            out = outcomes[len(calls) - 1]
            res = {"outcome": out, "price": 100 if out == "deal" else None,
                   "receipt": {"item": "lamp", "price": 100, "task": "t"} if out == "deal" else None, "rounds": 1}
            return res, {"shop": chosen["name"], "outcome": out, "item": "lamp"}

        with TemporaryDirectory() as d:
            shops = [{"name": "fair-furniture", "url": "https://ff.example", "reviews": []},
                     {"name": "other", "url": "https://other.example", "reviews": []}]
            sp = _P(d) / "shops.json"; sp.write_text(_json.dumps(shops))
            argv = ["market_buyer.py", "--shops", str(sp), "--item", "lamp", "--budget", "120",
                    "--memory", str(_P(d) / "mem.jsonl")]
            out = io.StringIO()
            with mock.patch.object(mb, "trade", fake_trade), mock.patch.object(mb, "kit_feedback", lambda: {}), \
                    mock.patch.object(mb, "load_shop_prices", lambda: {}), mock.patch.object(mb, "log_review_spam", lambda s: None), \
                    mock.patch.object(_sys, "argv", argv), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                mb.main()
            return calls, _json.loads(out.getvalue().strip().splitlines()[-1])

    def test_error_retries_next_shop(self):
        calls, line = self._run(["error", "deal"])
        self.assertEqual(calls, ["fair-furniture", "other"])
        self.assertEqual(line["shop"], "https://other.example")
        self.assertEqual(line["receipt"]["price"], 100)

    def test_walk_is_not_retried(self):
        calls, line = self._run(["walk", "deal"])
        self.assertEqual(calls, ["fair-furniture"])
        self.assertIsNone(line["receipt"])
