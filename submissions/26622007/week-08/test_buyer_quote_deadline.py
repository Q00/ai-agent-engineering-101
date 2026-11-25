"""Goods quotes end 13 s into the run when the switch is on (review/W-20261108-2135-AX거래량: the market hands the next
order 20.8 s after the last at the earliest; goods quotes waiting past it cost 7.3 orders/h, and the shop a goods deal
was made with had answered by 14 s in 111 of 113 deals)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_quote_deadline as qd   # noqa: E402
import market_buyer                 # noqa: E402


def shop(n, sku="H1", lp=10000):
    return {"name": n, "url": f"https://{n}.example", "reviews": [], "usable": [{"sku": sku, "name": "청소기", "list_price": lp}]}


def open_task(n):
    return {"id": f"t-{n}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}


class Switch(unittest.TestCase):
    def md(self, body):
        d = tempfile.mkdtemp()
        if body is not None:
            with open(os.path.join(d, qd.SWITCH), "w", encoding="utf-8") as f:
                f.write(body if isinstance(body, str) else json.dumps(body))
        return d

    def test_off_unless_the_file_says_enabled_true(self):
        for body in (None, "not json", {"enabled": "yes"}, {"enabled": False}, []):
            self.assertIsNone(qd.state(self.md(body), {"kind": "goods"}, 100.0), body)

    def test_on_for_goods_only_at_started_plus_seconds(self):
        md = self.md({"enabled": True})
        self.assertEqual(qd.state(md, {"kind": "goods"}, 100.0), {"at": 100.0 + qd.SECONDS, "seconds": qd.SECONDS, "cut": False})
        self.assertIsNone(qd.state(md, {"kind": "food"}, 100.0))
        self.assertIsNone(qd.state(md, {}, 100.0))

    def test_seconds_from_the_switch_within_bounds_else_default(self):
        self.assertEqual(qd.state(self.md({"enabled": True, "seconds": 15}), {"kind": "goods"}, 0.0)["seconds"], 15.0)
        for bad in (3, 40, "15", True, None):
            self.assertEqual(qd.state(self.md({"enabled": True, "seconds": bad}), {"kind": "goods"}, 0.0)["seconds"],
                             qd.SECONDS, bad)

    def test_env_zero_is_off(self):
        md = self.md({"enabled": True})
        self.assertIsNone(qd.state(md, {"kind": "goods"}, 0.0, env={"BUYER_QUOTE_DEADLINE": "0"}))

    def test_clip(self):
        self.assertEqual(qd.clip({}, 50.0), 50.0)
        self.assertEqual(qd.clip({"_quote_deadline": {"at": 40.0}}, 50.0), 40.0)
        self.assertEqual(qd.clip({"_quote_deadline": {"at": 60.0}}, 50.0), 50.0)


class BestQuoteDeadline(unittest.TestCase):
    def run_quotes(self, order, slow_ask=7000, fast_ask=None, wait=3.0):
        """a answers at once, b after 1.5 s; the batch would wait up to `wait` for b (fewer than QUOTE_ENOUGH asks)."""
        gate = threading.Event()

        def fake_quote(s, label, key, o, out, ctl=None):
            if s["name"] == "b":
                gate.wait(1.5)
            ask = fast_ask if s["name"] == "a" else slow_ask
            q = {"ask": ask, "text": "", "url": s["url"], "task": open_task(s["name"]), "refused": None}
            out[s["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != s["name"]:
                market_buyer.cancel_quote(q, s["name"])
        s = [shop("a"), shop("b")]
        t0 = time.time()
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
                mock.patch.object(market_buyer, "STARTED", t0):
            best, got = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 2},
                                                wait=wait, rounds=1)
            took = time.time() - t0
            gate.set()
            time.sleep(0.1)
        return best, got, took

    def test_without_the_deadline_the_slow_shop_is_waited_for(self):
        best, got, took = self.run_quotes({"kind": "goods"})
        self.assertGreaterEqual(took, 1.4)
        self.assertEqual(best["name"], "b")

    def test_the_deadline_ends_the_wait_and_says_it_cut(self):
        order = {"kind": "goods", "_quote_deadline": {"at": time.time() + 0.4, "seconds": 0.4, "cut": False}}
        best, got, took = self.run_quotes(order, fast_ask=7400)
        self.assertLess(took, 1.2)
        self.assertEqual(best["name"], "a")              # the answer in hand
        self.assertTrue(order["_quote_deadline"]["cut"])
        self.assertEqual(order["_quote_deadline"]["left"], 1)

    def test_a_deadline_after_the_quotes_changes_nothing(self):
        order = {"kind": "goods", "_quote_deadline": {"at": time.time() + 30, "seconds": 30, "cut": False}}
        best, got, took = self.run_quotes(order)
        self.assertEqual(best["name"], "b")
        self.assertFalse(order["_quote_deadline"]["cut"])

    def test_past_the_deadline_at_the_start_no_new_shop_is_asked(self):
        # Codex 21:5x: a choice that ends after the deadline must not start the first batch (the chosen shop)
        asked = []

        def fake_quote(s, label, key, o, out, ctl=None):
            asked.append(s["name"])
            q = {"ask": 7000, "text": "", "url": s["url"], "task": open_task(s["name"]), "refused": None}
            out[s["name"]] = q
            if ctl is not None and ctl.get("done") and ctl.get("winner") != s["name"]:
                market_buyer.cancel_quote(q, s["name"])
        s = [shop(n) for n in "abc"]
        order = {"kind": "goods", "_quote_deadline": {"at": time.time() - 1, "seconds": 13, "cut": False}}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            pre = {"got": {}, "ctl": market_buyer.quote_ctl()}
            pre["threads"] = market_buyer.start_quotes(s[1:], order, 20000, lambda x: None, pre["got"], pre["ctl"])
            time.sleep(0.2)
            best, got = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 3},
                                                wait=2, pre=pre)
            time.sleep(0.1)
        self.assertEqual(sorted(asked), ["b", "c"])       # a (chosen, not prefetched) never asked
        self.assertIn(best["name"], ("b", "c"))

    def test_no_prefetch_and_past_the_deadline_asks_no_shop(self):
        order = {"kind": "goods", "_quote_deadline": {"at": time.time() - 1, "seconds": 13, "cut": False}}
        at = order["_quote_deadline"]["at"]
        chosen, other = shop("a"), shop("b")
        with mock.patch.object(market_buyer, "quote") as quote, \
                mock.patch.object(market_buyer, "buyer_rpc") as rpc, \
                mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            started = time.monotonic()
            best, got = market_buyer.best_quote(chosen, [other], order, {}, 20000, lambda x: None,
                                                {"quote_shops": 2}, rounds=2)
            took = time.monotonic() - started
        quote.assert_not_called()
        rpc.assert_not_called()
        self.assertLess(took, 0.5)
        self.assertIs(best, chosen)
        self.assertEqual(got, {})
        self.assertEqual(order["_quote_deadline"]["at"], at)
        self.assertEqual(order["_quote_deadline"]["skipped"], "past at the start")
        json.dumps(order)


if __name__ == "__main__":
    unittest.main()
