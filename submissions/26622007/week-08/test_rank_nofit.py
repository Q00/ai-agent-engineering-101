import json
import os
import tempfile
import time
import unittest
from unittest import mock

import buyer_rank_pg
import market_buyer


def row(shop, ok, fit, kind="goods", ts=None, ask=8000):
    return {"ts": ts or time.time() - 60, "shop": shop, "kind": kind, "order": f"o-{shop}-{ok}-{fit}-{id(object())}",
            "state": "TASK_STATE_COMPLETED" if ok else "TASK_STATE_FAILED", "price": 7000 if ok else None,
            "passed_to_kit": ok, "quotes": [{"shop": shop, "ask": ask, "limit": 7000, "fit": fit}],
            "rank_selection": {"version": 2, "tier": "normal", "eligible": not fit, "shops": [shop]},
            "seconds": {"quotes": 1, "end": 3}}


def confirmed(rows):
    # Unit-level exact-join outcomes; integration joins are covered by test_rank_integrity's production-schema fixture.
    return {r["order"]: "confirmed" for r in rows if r["passed_to_kit"]}


class NoFitStatsTest(unittest.TestCase):
    """P of a deal when no quote of the order was within my limit, per shop (24h 11/08: 1,794 such goods orders, mjforge
    picked 411 times for 2 paid, ddolcom 221 for 114; review/W-20261108-2241-AXPro주기)."""

    def test_only_orders_with_no_ask_within_the_limit_count(self):
        rows = [row("m", False, False)] * 10 + [row("m", True, True)] * 10 + [row("d", True, False)] * 10
        st = buyer_rank_pg.stats_from_rows(rows, confirmed(rows), time.time())
        nf = st["nofit"]
        self.assertEqual((nf["shops"]["m|goods"]["n"], nf["shops"]["m|goods"]["k"]), (10, 0))
        self.assertEqual((nf["shops"]["d|goods"]["n"], nf["shops"]["d|goods"]["k"]), (10, 10))
        self.assertAlmostEqual(nf["pool"]["goods"]["p"], 0.5)
        self.assertLess(buyer_rank_pg.nofit_p({"stats": st}, "m", "goods"), buyer_rank_pg.nofit_p({"stats": st}, "d", "goods"))
        self.assertAlmostEqual(buyer_rank_pg.nofit_p({"stats": st}, "z", "goods"), 0.5)     # unknown shop: the pool

    def test_only_the_last_nofit_hours_count(self):
        old = time.time() - (buyer_rank_pg.NOFIT_HOURS + 1) * 3600
        rows = [row("d", True, False, ts=old)] * 10 + [row("d", False, False)] * 2
        nf = buyer_rank_pg.stats_from_rows(rows, confirmed(rows), time.time())["nofit"]
        self.assertEqual((nf["shops"]["d|goods"]["n"], nf["shops"]["d|goods"]["k"]), (2, 0))

    def test_a_kept_ask_over_the_band_or_none_does_not_count(self):
        rows = [row("m", False, False, ask=9000)] * 5 + [row("m", False, False, ask=None)] * 5 + [row("m", True, False)] * 2
        nf = buyer_rank_pg.stats_from_rows(rows, confirmed(rows), time.time())["nofit"]
        self.assertEqual((nf["shops"]["m|goods"]["n"], nf["shops"]["m|goods"]["k"]), (2, 2))

    def test_ab_splits_the_orders_by_id(self):
        sw = {"enabled": True, "nofit": "ab", "nofit_version": 2, "nofit_experiment": "unit", "nofit_epoch": 1794140000}
        arms = {buyer_rank_pg.nofit_arm(sw, {"order": f"o-{i:04d}"}) for i in range(40)}
        self.assertEqual(arms, {"A", "B"})
        self.assertEqual(buyer_rank_pg.nofit_arm(sw, {"order": "o-x"}), buyer_rank_pg.nofit_arm(sw, {"order": "o-x"}))
        self.assertIsNone(buyer_rank_pg.nofit_arm(sw, {}))
        self.assertIsNone(buyer_rank_pg.nofit_arm({"enabled": True, "nofit": True}, {"order": "o-x"}))
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, buyer_rank_pg.SWITCH), "w") as f:
                json.dump(sw, f)
            with mock.patch.object(buyer_rank_pg, "stats", lambda md, now=None: {"pool": {}, "shops": {}, "nofit": {"pool": {"goods": {"p": 0.2}}, "shops": {}}}):
                for i in range(20):
                    st = buyer_rank_pg.state(md, {"order": f"o-{i}"})
                    self.assertEqual(st["nofit"], st["nofit_arm"] == "B")

    def test_switch_turns_it_on(self):
        with tempfile.TemporaryDirectory() as md:
            with open(os.path.join(md, buyer_rank_pg.SWITCH), "w") as f:
                json.dump({"enabled": True, "nofit": True}, f)
            with mock.patch.object(buyer_rank_pg, "stats", lambda md, now=None: {"pool": {}, "shops": {}}):
                self.assertFalse(buyer_rank_pg.state(md, {})["nofit"])  # unversioned legacy switch stays off
            with open(os.path.join(md, buyer_rank_pg.SWITCH), "w") as f:
                json.dump({"enabled": True}, f)
            with mock.patch.object(buyer_rank_pg, "stats", lambda md, now=None: {"pool": {}, "shops": {}}):
                self.assertFalse(buyer_rank_pg.state(md, {})["nofit"])


class NoFitPickTest(unittest.TestCase):
    """No ask within my limit: with "nofit" on, the shop likeliest to come down to it (within the talk band) is talked to,
    not the cheapest ask; off, or no shop in the band, the order as before."""
    S = 0.3

    def run_bq(self, asks, rk, kind="goods"):
        s = [{"name": n, "url": f"https://{n}.example", "usable": [{"sku": "H1", "name": "x", "qty": 1, "list_price": 10000}]}
             for n in asks]
        order = {"kind": kind, "_sw": {"S": self.S, "hist": {}}, "_rank_pg": rk}

        def fake_quote(sh, label, key, order, out, ctl=None):
            out[sh["name"]] = {"ask": asks[sh["name"]], "text": "", "refused": None}
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda q, n: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()), \
                mock.patch.dict(market_buyer._SW_CTX, {"catalog": None}):
            best, _ = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 3}, wait=2)
        return best["name"]

    def rk(self, nofit=True):
        st = {"pool": {"goods": {"p": 0.2, "ratio": 1.0, "talk": None}}, "shops": {},
              "nofit": {"pool": {"goods": {"p": 0.2}}, "shops": {"b|goods": {"p": 0.01}, "c|goods": {"p": 0.5},
                                                              "d|goods": {"p": 0.9}}}}
        return {"stats": st, "nofit": nofit}

    def test_the_likeliest_shop_in_the_band_is_talked_to(self):
        rk = self.rk()
        self.assertEqual(self.run_bq({"b": 7400, "c": 8000, "d": 9000}, rk), "c")
        self.assertTrue(rk["no_fit"])
        self.assertEqual(rk["picked"], "c")
        self.assertEqual(rk["plain"], "b")

    def test_a_shop_over_the_band_is_not_picked(self):
        # d's ask 9,000 is over 7,000 x 1.2: that order would end without a talk (NO_VALUE, rank-cause.md)
        self.assertEqual(self.run_bq({"b": 7400, "c": 9500, "d": 9000}, self.rk()), "b")

    def test_off_is_the_current_order(self):
        self.assertEqual(self.run_bq({"b": 7400, "c": 8000, "d": 9000}, self.rk(nofit=False)), "b")

    def test_a_cache_without_nofit_keeps_the_current_order(self):
        rk = self.rk()
        del rk["stats"]["nofit"]
        self.assertEqual(self.run_bq({"b": 7400, "c": 8000, "d": 9000}, rk), "b")

    def test_food_keeps_the_current_order(self):
        self.assertEqual(self.run_bq({"b": 7400, "c": 8000, "d": 9000}, self.rk(), kind="food"), "b")

    def test_an_ask_within_the_limit_still_wins(self):
        self.assertEqual(self.run_bq({"b": 6600, "c": 8000, "d": 9000}, self.rk()), "b")


if __name__ == "__main__":
    unittest.main()
