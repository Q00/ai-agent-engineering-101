import time
import unittest
from unittest import mock

import market_buyer


class ShortWarrantyRouteTest(unittest.TestCase):
    """buyer_short_warranty on keeps the shops whose last receipts were short (skip_short_warranty used to leave them out)
    and prices them as 0 h. 11/08 20:15-21:2x 69 of 93 goods orders went to one of them and 4 were deals, the other 24 13
    (review/W-20261108-1915-AX전부적용/rank-cause.md): a shop expected to give a 6 h warranty goes first, as when they
    were left out; a short one is picked only when no other shop quoted."""
    S = 0.3

    def run_bq(self, asks, hist, sw=True, rk=None):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in asks]
        order = {"kind": "goods", **({"_sw": {"S": self.S, "hist": dict(hist)}} if sw else {}),
                 **({"_rank_pg": rk} if rk is not None else {})}

        def fake_quote(sh, label, key, order, out, ctl=None):
            out[sh["name"]] = {"ask": asks[sh["name"]], "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()), \
                mock.patch.dict(market_buyer._SW_CTX, {"catalog": None}):
            best, _ = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 3}, wait=2)
        return best["name"]

    def test_a_normal_shop_goes_before_a_short_one(self):
        # a: short, 4,500 within its 0 h cap and the most kept at the fallback break chance; b: normal, 6,600
        self.assertEqual(self.run_bq({"a": 4500, "b": 6600, "c": 9900}, {"a": 0}), "b")

    def test_a_normal_shop_over_the_limit_still_goes_first(self):
        # as when a was left out: b is talked to even at an ask over its limit
        self.assertEqual(self.run_bq({"a": 4500, "b": 7400, "c": 9900}, {"a": 0}), "b")

    def test_only_short_shops_quoted_picks_the_best_of_them(self):
        self.assertEqual(self.run_bq({"a": 4500, "b": 6600, "c": 9900}, {"a": 0, "b": 0, "c": 0}), "a")

    def test_rank_on_with_no_normal_ask_within_the_limit_keeps_the_current_order(self):
        # a (short) fits, b and c (normal) are over the limit: rank_pg must not rank b and c by P x g (rank-cause.md)
        stats = {"pool": {"goods": {"p": 0.5, "ratio": 1.0, "talk": None}},
                 "shops": {"b|goods": {"p": 0.1, "ratio": 1.0, "talk": None}, "c|goods": {"p": 1.0, "ratio": 0.5, "talk": None}}}
        order_rk = {"stats": stats}
        self.assertEqual(self.run_bq({"a": 4500, "b": 7400, "c": 9000}, {"a": 0}, rk=order_rk), "b")
        self.assertTrue(order_rk.get("no_fit"))

    def test_switch_off_is_the_current_order(self):
        self.assertEqual(self.run_bq({"a": 4500, "b": 6600, "c": 9900}, {"a": 0}, sw=False), "a")


if __name__ == "__main__":
    unittest.main()
