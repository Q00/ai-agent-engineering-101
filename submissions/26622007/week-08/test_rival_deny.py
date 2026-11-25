"""My buyer asks the revenue rivals of my shop last and ranks them MARGIN lower (rival_deny)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import market_buyer                # noqa: E402
import rival_deny                  # noqa: E402


def shop(n, sku="H1", lp=10000):
    return {"name": n, "url": f"https://{n}.example", "reviews": [], "usable": [{"sku": sku, "name": "소파", "list_price": lp}]}


def snap(ts, shops):
    return json.dumps({"ts": ts, "players": {}, "shops": shops, "buyers": {}})


class Rivals(unittest.TestCase):
    def write(self, lines):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, rival_deny.HISTORY), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return d

    def test_shops_at_or_above_the_share_of_my_revenue_from_the_newest_snapshot(self):
        now = time.time()
        d = self.write([snap(now - 7200, {"nocha12": [1, 100, 0.5], "old": [1, 500, 0.5]}),
                        snap(now - 60, {"nocha12": [1, 100, 0.5], "inextro": [1, 900, 0.9], "near": [1, 90, 0.5],
                                        "below": [1, 89, 0.5], "odd": None})])
        self.assertEqual(rival_deny.rivals(d, now), {"inextro", "near"})

    def test_a_stale_missing_or_broken_board_means_no_rivals(self):
        now = time.time()
        self.assertEqual(rival_deny.rivals(self.write([snap(now - 4 * 3600, {"nocha12": [1, 1, 1], "x": [1, 9, 1]})]), now), set())
        self.assertEqual(rival_deny.rivals(tempfile.mkdtemp(), now), set())
        self.assertEqual(rival_deny.rivals(self.write(["{not json", snap(now, {"x": [1, 9, 1]})]), now), set())   # I am not on it
        self.assertEqual(rival_deny.rivals(self.write([json.dumps({"ts": now, "shops": "x"})]), now), set())
        self.assertEqual(rival_deny.rivals(self.write([json.dumps({"ts": now, "shops": [1, 2]})]), now), set())

    def test_others_first_keeps_each_part_in_order(self):
        s = [shop(n) for n in "abcde"]
        self.assertEqual([x["name"] for x in rival_deny.others_first(s, {"a", "c"})], list("bdeac"))


class Ranked(unittest.TestCase):
    def run_quotes(self, asks, chosen, others, rival, k=5):
        seen, lock = [], threading.Lock()

        def fake_quote(s, label, key, order, out, ctl=None):
            with lock:
                seen.append(s["name"])
            out[s["name"]] = {"ask": asks.get(s["name"]), "text": "", "url": s["url"],
                              "task": {"id": f"t-{s['name']}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}, "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            best, got = market_buyer.best_quote(chosen, others, {"kind": "goods"}, {}, 20000, lambda s: None,
                                                {"quote_shops": k}, wait=2, rival=rival)
        return best, seen

    def test_a_fitting_non_rival_beats_a_slightly_cheaper_rival(self):
        s = [shop(n) for n in "abc"]
        best, _ = self.run_quotes({"a": 6000, "b": 6500, "c": 9000}, s[0], s[1:], {"a"})
        self.assertEqual(best["name"], "b")          # a keeps 0.40, b 0.35: within MARGIN, so b

    def test_a_rival_that_leaves_much_more_still_wins(self):
        s = [shop(n) for n in "ab"]
        best, _ = self.run_quotes({"a": 4000, "b": 6500}, s[0], s[1:], {"a"})
        self.assertEqual(best["name"], "a")          # 0.60 - 0.10 > 0.35

    def test_a_cheaper_rival_in_a_later_batch_is_not_asked_once_a_non_rival_fits(self):
        s = [shop(n) for n in "abc"]
        best, seen = self.run_quotes({"a": 9000, "b": 6500, "c": 3000}, s[0], s[1:], {"c"}, k=2)
        self.assertEqual(best["name"], "b")
        self.assertNotIn("c", seen)

    def test_with_no_rivals_the_cheapest_wins_as_before(self):
        s = [shop(n) for n in "ab"]
        best, _ = self.run_quotes({"a": 6000, "b": 6500}, s[0], s[1:], set())
        self.assertEqual(best["name"], "a")

    def test_prefetch_pool_asks_rivals_after_the_others(self):
        s = [shop(n) for n in "abcd"]
        with mock.patch.object(market_buyer, "recent_fits", lambda md, c: __import__("collections").Counter()):
            early, pool = market_buyer.prefetch_pool(s, {"trades": []}, ".", "소파", 3, {"a", "b"})
        self.assertEqual([x["name"] for x in early], ["c", "d"])
        self.assertEqual([x["name"] for x in pool][-2:], ["a", "b"])



class OffTest(unittest.TestCase):
    """Off since 11/07 00:30: with best_quote stopping at QUOTE_ENOUGH asks, the rivals asked last were often never asked.
    In 26 orders cancelled with no quote within my limit (11/06 23:00-00:25), a mean 3.3 rival candidates went unasked, and
    rival quotes fitted as often as the others (19 of 214 against 37 of 453)."""

    def test_main_does_not_use_the_rivals(self):
        import inspect
        import market_buyer
        self.assertFalse(market_buyer.RIVAL_DENY)
        self.assertIn("RIVAL_DENY", inspect.getsource(market_buyer.main))


if __name__ == "__main__":
    unittest.main()
