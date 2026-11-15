"""best_quote's fit is the price the talk can close at: with the margin cap take on, an ask over my limit and within
min(cap, budget) is closed by the talk (buyer.talk walk_take, buyer_accept_band), so it fits, as main's take_top."""
import time
import unittest
from unittest import mock

import market_buyer
from test_buyer_margin_cap import Run, shop

TAKE = {"cfg": {"enabled": True, "target": 0.75, "take": True}, "stats": None}
NO_TAKE = {"cfg": {"enabled": True, "target": 0.75}, "stats": None}
# list 10,000, goods at target .75: grade A's limit 6799 (saved_cap), its cap 7293 (b .03); grade B's cap 5762 (b .115)
A_TAKE = (7200, "G1, grade A: asking 7200")     # over A's limit, within its cap: only the take closes it
B_FITS = (5700, "G1, grade B: asking 5700")     # within B's cap: fits either way, keeps .767 against A's .790


def pick(asks, margin, order=None, budget=20000):
    def fake_quote(s, label, key, o, out, ctl=None):
        ask, text = asks[s["name"]]
        out[s["name"]] = {"ask": ask, "text": text, "url": s["url"], "refused": None,
                          "task": {"id": f"t-{s['name']}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
    s = [shop(n) for n in asks]
    order = {"kind": "goods", **(order or {})}
    with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
            mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
            mock.patch.object(market_buyer, "cancel_quietly", lambda *a, **k: None), \
            mock.patch.object(market_buyer, "STARTED", time.time()), \
            mock.patch.object(market_buyer, "MARGIN_CAP", margin):
        best, _ = market_buyer.best_quote(s[0], s[1:], order, {}, budget, lambda x: None,
                                          {"quote_shops": len(s)}, wait=1.0, rounds=1)
    return best["name"], order


class Fit(unittest.TestCase):
    def test_take_on_an_ask_within_the_take_fits_and_keeps_more(self):
        name, order = pick({"b": B_FITS, "a": A_TAKE}, TAKE)
        self.assertEqual(name, "a")
        self.assertIs(order["_rank_selection"]["eligible"], False)     # a fit: not a no-fit order

    def test_take_off_the_old_limit_holds(self):
        self.assertEqual(pick({"b": B_FITS, "a": A_TAKE}, NO_TAKE)[0], "b")

    def test_over_the_cap_does_not_fit(self):
        self.assertEqual(pick({"b": B_FITS, "a": (7300, "G1, grade A: asking 7300")}, TAKE)[0], "b")

    def test_the_budget_holds_the_take(self):
        self.assertEqual(pick({"b": B_FITS, "a": A_TAKE}, TAKE, budget=7100)[0], "b")

    def test_a_saved_floor_without_the_band_has_no_take(self):
        self.assertEqual(pick({"b": B_FITS, "a": A_TAKE}, TAKE, order={"_saved_floor": {"band_off": True}})[0], "b")


class CloseLimit(unittest.TestCase):
    O = {"sku": "G1", "list_price": 10000}
    QA = {"ask": 7200, "text": "G1, grade A: asking 7200"}

    def lim(self, margin, order=None, budget=20000, sc=None):
        with mock.patch.object(market_buyer, "MARGIN_CAP", margin), \
                mock.patch.object(market_buyer, "short_cap", lambda *a, **k: sc):
            return market_buyer.close_limit(self.O, None, {"kind": "goods", **(order or {})}, "a", self.QA, None,
                                            budget, {})

    def test_take_on_is_min_cap_budget(self):
        self.assertEqual(self.lim(TAKE), 7293)
        self.assertEqual(self.lim(TAKE, budget=7000), 7000)

    def test_off_or_no_take_is_the_old_limit(self):
        self.assertEqual(self.lim(None), 6799)
        self.assertEqual(self.lim(NO_TAKE), 6799)

    def test_a_short_warranty_keeps_its_cap_and_no_take(self):
        self.assertEqual(self.lim(TAKE, sc=6000), 6000)
        self.assertEqual(self.lim(TAKE, sc=0), 0)

    def test_a_food_quote_without_a_grade_keeps_the_limit(self):
        q = {"ask": 7000, "text": "G1: asking 7000"}
        with mock.patch.object(market_buyer, "MARGIN_CAP", TAKE):
            self.assertEqual(market_buyer.close_limit(self.O, None, {"kind": "food"}, "a", q, None, 20000, {}), 6799)

    def test_take_false_is_the_limit_without_the_take(self):
        with mock.patch.object(market_buyer, "MARGIN_CAP", TAKE):
            self.assertEqual(market_buyer.close_limit(self.O, None, {"kind": "goods"}, "a", self.QA, None, 20000, {},
                                                      take=False), 6799)

    def test_a_cap_under_the_limit_lowers_it(self):
        q = {"ask": 5700, "text": "G1, grade B: asking 5700"}
        with mock.patch.object(market_buyer, "MARGIN_CAP", TAKE):
            self.assertEqual(market_buyer.close_limit(self.O, None, {"kind": "goods"}, "b", q, None, 20000, {}), 5762)


class GainBase(unittest.TestCase):
    """buyer_rank_pg gain_base: P x (g - base), a deal moving Saved by (g - S) / (deals + 101)."""
    RK = {"stats": {"shops": {"sure|goods": {"p": 0.9, "ratio": 1.0}, "good|goods": {"p": 0.5, "ratio": 1.0}}}}

    def s(self, shop, g, base):
        import buyer_rank_pg
        return buyer_rank_pg.score(self.RK, shop, "goods", 5000, 10000, lambda p: g, 0.0, 0.0, base)

    def test_without_base_the_likely_deal_wins_as_before(self):
        self.assertGreater(self.s("sure", 0.75, None), self.s("good", 0.85, None))

    def test_with_base_the_deal_that_raises_saved_wins(self):
        self.assertAlmostEqual(self.s("sure", 0.75, 0.72), 0.9 * 0.03)
        self.assertGreater(self.s("good", 0.85, 0.72), self.s("sure", 0.75, 0.72))

    def test_under_the_base_ranks_by_the_expected_change_below_any_over_it(self):
        self.assertAlmostEqual(self.s("sure", 0.70, 0.72), 0.9 * -0.02)
        self.assertLess(self.s("sure", 0.70, 0.72), self.s("good", 0.70, 0.72))     # the less likely harms less
        self.assertLess(self.s("good", 0.71, 0.72), self.s("sure", 0.73, 0.72))
        self.assertEqual(self.s("good", -0.1, None), -0.1)                           # without base: g alone, as before
        self.assertEqual(self.s("good", -0.1, None), self.s("sure", -0.1, None))

    def test_best_quote_passes_the_base_only_with_the_margin_cap(self):
        # A at 65% keeps .912 (P .5), B at 50% .846 (P .6): P x g picks b, P x (g - .72) picks a
        asks = {"b": (5000, "G1, grade B: asking 5000"), "a": (6500, "G1, grade A: asking 6500")}
        def rk(base):
            return {"stats": {"shops": {"a|goods": {"p": 0.5, "ratio": 1.0}, "b|goods": {"p": 0.6, "ratio": 1.0}}},
                    "per_time": False, **({"gain_base": base} if base else {})}
        self.assertEqual(pick(asks, TAKE, order={"_rank_pg": rk(None)})[0], "b")
        name, order = pick(asks, TAKE, order={"_rank_pg": rk(0.72)})
        self.assertEqual(name, "a")
        self.assertEqual(order["_rank_pg"]["picked"], "a")

    def test_switch_reads_a_sane_base_only(self):
        import json, os, tempfile, buyer_rank_pg
        for v, want in ((0.72, 0.72), (1.5, None), (0, None), (-0.1, None), ("0.72", None), (True, None), (None, None)):
            md = tempfile.mkdtemp()
            with open(os.path.join(md, buyer_rank_pg.SWITCH), "w") as f:
                json.dump({"enabled": True, **({"gain_base": v} if v is not None else {})}, f)
            with mock.patch.object(buyer_rank_pg, "stats", lambda md, now: {}):
                self.assertEqual(buyer_rank_pg.state(md, {"order": "o"}, env={}).get("gain_base"), want, v)


class MainBand(Run, unittest.TestCase):
    """main's talk band reads the take, as best_quote's reach (review 1b86f962 I1/I2): a grade A unit of a 7,600 list,
    limit 5167, take 5543; the ask 6500 is over 1.2 x 5167 = 6200 and within 1.2 x 5543 = 6651."""
    TEXT = "컵 (G1), grade A, 6 h warranty: asking 6500"
    CATALOG = {"G1": {"kind": "goods", "category": "가전>컵", "list_price": 7600}}

    def test_take_on_the_ask_within_the_takes_band_is_talked_to(self):
        row, calls = self.run_main({"enabled": True, "take": True})
        self.assertEqual((row["limit"], row["margin_cap"]["take_top"]), (5167, 5543))
        self.assertEqual(len(calls), 1)

    def test_take_off_it_is_not_as_before(self):
        row, calls = self.run_main({"enabled": True})
        self.assertEqual(calls, [])
        self.assertEqual(row["state"], "NO_VALUE")


if __name__ == "__main__":
    unittest.main()
