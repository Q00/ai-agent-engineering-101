"""A shop whose last two deals with me went unpaid is not quoted for UNPAID_COOL after the last (11/10 dragonash 402s)."""
import os
import tempfile
import time
import unittest
from unittest import mock

import market_buyer as mb

NOW = 1794299400       # 11/10 17:30 KST


def trade(result, ago):
    return {"shop": "dragonash", "result": result, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago))}


class UnpaidLately(unittest.TestCase):
    def test_two_unpaid_under_cool_skip(self):
        self.assertTrue(mb.unpaid_lately([trade("deal", 900), trade("unpaid", 600), trade("unpaid", 60)], NOW))

    def test_after_cool_it_is_asked_again(self):
        self.assertFalse(mb.unpaid_lately([trade("unpaid", 3000), trade("unpaid", mb.UNPAID_COOL + 1)], NOW))

    def test_one_unpaid_or_a_paid_deal_last_is_no_skip(self):
        self.assertFalse(mb.unpaid_lately([trade("deal", 600), trade("unpaid", 60)], NOW))
        self.assertFalse(mb.unpaid_lately([trade("unpaid", 600), trade("deal", 60)], NOW))
        self.assertFalse(mb.unpaid_lately([trade("unpaid", 60)], NOW))
        self.assertFalse(mb.unpaid_lately([], NOW))

    def test_other_failures_are_not_this_rule(self):
        self.assertFalse(mb.unpaid_lately([trade("error", 600), trade("unpaid", 60)], NOW))
        self.assertFalse(mb.unpaid_lately([trade("no_deal", 600), trade("no_deal", 60)], NOW))

    def test_a_time_it_cannot_read_is_no_skip(self):
        bad = trade("unpaid", 60)
        bad["time"] = "yesterday"
        self.assertFalse(mb.unpaid_lately([trade("unpaid", 600), bad], NOW))
        del bad["time"]
        self.assertFalse(mb.unpaid_lately([trade("unpaid", 600), bad], NOW))


class Prefetch(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(mb.time, "time", return_value=NOW)
        p.start()
        self.addCleanup(p.stop)

    def pool(self, trades, order=None):
        shops = [{"name": n, "reviews": [{}] * r, "usable": []} for n, r in (("dragonash", 50), ("yms979", 10), ("kiro", 5))]
        with tempfile.TemporaryDirectory() as md, mock.patch.object(mb, "log"):
            os.mkdir(os.path.join(md, "measure"))
            _, everyone = mb.prefetch_pool(shops, {"trades": trades}, md, "c", 3, kind="food", order_id="o-0", order=order)
        return [s["name"] for s in everyone]

    def test_quotes_skip_a_shop_unpaid_twice_lately_and_record_it(self):
        order = {}
        names = self.pool([trade("unpaid", 600), trade("unpaid", 60)], order)
        self.assertNotIn("dragonash", names)
        self.assertEqual(sorted(names), ["kiro", "yms979"])
        self.assertEqual(order["_unpaid_skip"], ["dragonash"])

    def test_quotes_ask_it_again_after_cool_or_a_paid_deal(self):
        order = {}
        self.assertIn("dragonash", self.pool([trade("unpaid", 3600), trade("unpaid", mb.UNPAID_COOL + 5)], order))
        self.assertEqual(order["_unpaid_skip"], [])
        self.assertIn("dragonash", self.pool([trade("unpaid", 600), trade("deal", 60)]))

    def test_every_shop_skipped_leaves_an_empty_pool(self):
        shops = [{"name": "dragonash", "reviews": [], "usable": []}]
        order = {}
        with tempfile.TemporaryDirectory() as md, mock.patch.object(mb, "log"):
            os.mkdir(os.path.join(md, "measure"))
            early, everyone = mb.prefetch_pool(shops, {"trades": [trade("unpaid", 600), trade("unpaid", 60)]}, md, "c", 3,
                                               kind="food", order_id="o-0", order=order)
        self.assertEqual((early, everyone), ([], []))
        self.assertEqual(order["_unpaid_skip"], ["dragonash"])

    def test_a_cheater_is_still_left_out_and_not_counted_as_unpaid(self):
        order = {}
        cheat = dict(trade("deal", 60), problems=["agreed 1, receipt says 2"])
        names = self.pool([trade("unpaid", 600), trade("unpaid", 300), cheat], order)
        self.assertNotIn("dragonash", names)
        self.assertEqual(order["_unpaid_skip"], [])


if __name__ == "__main__":
    unittest.main()
