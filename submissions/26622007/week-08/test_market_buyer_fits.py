import json, os, tempfile, time, unittest

import market_buyer


class RecentFitsTest(unittest.TestCase):
    def test_counts_asks_within_my_limit_for_the_category_in_the_window(self):
        now = time.time()
        rows = [{"ts": now - 60, "category": "c", "budget": 50,
                 "quotes": [{"shop": "a", "ask": 10, "list_price": 20, "lost": None},
                            {"shop": "b", "ask": 12, "list_price": 20, "lost": "less expected surplus"},
                            {"shop": "c", "ask": 99, "list_price": 120, "lost": "over my limit (budget / share / deal cap)"},
                            {"shop": "d", "ask": None, "list_price": 20, "lost": "no ask (no free unit or no answer)"}]},
                # the chosen shop (lost None) asked over the budget, or over the list price: not a fit
                {"ts": now - 60, "category": "c", "budget": 30, "quotes": [{"shop": "e", "ask": 40, "list_price": 90, "lost": None},
                                                                          {"shop": "f", "ask": 25, "list_price": 20, "lost": None},
                                                                          {"shop": "g", "ask": 5, "lost": None}]},
                # lines with fit say so themselves
                {"ts": now - 60, "category": "c", "budget": 30, "quotes": [{"shop": "a", "ask": 40, "list_price": 90, "lost": None, "fit": True},
                                                                          {"shop": "h", "ask": 5, "list_price": 9, "lost": None, "fit": False}]},
                {"ts": now - 4 * 3600, "category": "c", "budget": 50, "quotes": [{"shop": "c", "ask": 5, "list_price": 9, "lost": None}]},
                {"ts": now - 60, "category": "other", "budget": 50, "quotes": [{"shop": "c", "ask": 5, "list_price": 9, "lost": None}]}]
        with tempfile.TemporaryDirectory() as md:
            os.makedirs(os.path.join(md, "measure"))
            with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
                f.writelines(json.dumps(r) + "\n" for r in rows)
                f.write('{"ts": cut')
            got = market_buyer.recent_fits(md, "c", now=now)
            self.assertEqual(dict(got), {"a": 2, "b": 1})
            self.assertEqual(market_buyer.recent_fits(md + "/none", "c"), {})
            self.assertEqual(market_buyer.recent_fits(md, None), {})

    def test_the_first_batch_keeps_one_slot_for_a_shop_that_has_not_fitted(self):
        import collections
        shop = lambda n, r: {"name": n, "reviews": [0] * r}
        others = [shop("new", 9), shop("fit1", 1), shop("old", 5), shop("fit2", 3)]
        fits = collections.Counter({"fit1": 52, "fit2": 1})
        order = [s["name"] for s in market_buyer.quote_order(others, fits, 3)]
        self.assertEqual(order, ["fit2", "new", "fit1", "old"])      # fitters by reviews, not by their fit count
        self.assertEqual([s["name"] for s in market_buyer.quote_order(others, fits, 2)], ["new", "fit2", "fit1", "old"])


class HopelessTest(unittest.TestCase):
    def write(self, md, rows):
        os.makedirs(os.path.join(md, "measure"), exist_ok=True)
        with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)

    def test_recent_asks_take_quotes_talk_and_receipts_in_the_window_not_refusals(self):
        now = time.time()
        rows = [{"ts": now - 60, "sku": "A", "asks": [90, 80], "price": 70,
                 "quotes": [{"shop": "a", "sku": "A", "ask": 95, "lost": None},
                            {"shop": "b", "sku": "B", "ask": 50, "lost": "less expected surplus"},
                            {"shop": "c", "sku": "B", "ask": 1, "lost": "refused: the shop sells it without a warranty"},
                            {"shop": "d", "sku": "B", "ask": None, "lost": "no ask (no free unit or no answer)"}]},
                {"ts": now - 4 * 3600, "sku": "B", "asks": [2], "quotes": []}]
        with tempfile.TemporaryDirectory() as md:
            self.write(md, rows)
            got = market_buyer.recent_asks(md, now=now)
            self.assertEqual(sorted(got["A"]), [70, 80, 90, 95])
            self.assertEqual(got["B"], [50])
            self.assertEqual(market_buyer.recent_asks(md + "/none"), {})

    def test_gives_up_only_when_every_shop_sku_asked_over_the_limit_enough_times(self):
        cfg = {"max_price_share": 1.0}
        shop = lambda n, sku, lp: {"name": n, "usable": [{"sku": sku, "list_price": lp}]}
        pool = [shop("a", "A", 100), shop("b", "B", 100)]          # limit 66 each (Saved .33, float truncation never rounds up)
        over = {"A": [80, 90], "B": [71, 99]}
        self.assertTrue(market_buyer.hopeless(pool, 1000, over, cfg))
        self.assertFalse(market_buyer.hopeless(pool, 1000, {**over, "B": [66, 99]}, cfg))     # one ask at the limit
        self.assertFalse(market_buyer.hopeless(pool, 1000, {**over, "B": [99]}, cfg))         # too few asks
        self.assertFalse(market_buyer.hopeless(pool, 1000, {"A": [80, 90]}, cfg))             # B never asked
        self.assertFalse(market_buyer.hopeless([], 1000, over, cfg))
        self.assertTrue(market_buyer.hopeless(pool, 65, {"A": [66, 72], "B": [66, 80]}, cfg))   # the budget is the limit
        self.assertFalse(market_buyer.hopeless(pool, 65, {"A": [65, 72], "B": [66, 80]}, cfg))

    def test_the_give_up_rule_stays_as_replayed(self):
        # pinned (11/06 replay: 3 h and 2 asks gave up 5 of 361 orders and none of the 165 paid; 6 h lost one paid order)
        # and called by main before the quotes; meta/terra.md says the meta-harness keeps it
        import inspect
        self.assertEqual((market_buyer.HOPELESS_HOURS, market_buyer.HOPELESS_MIN_ASKS), (3, 2))
        src = inspect.getsource(market_buyer.main)
        self.assertIn("hopeless(", src)
        self.assertIn("not gave_up", src)
        here = os.path.dirname(os.path.abspath(market_buyer.__file__))
        with open(os.path.join(here, "meta", "terra.md"), encoding="utf-8") as fh:
            self.assertIn("market_buyer.hopeless()", fh.read())


if __name__ == "__main__":
    unittest.main()
