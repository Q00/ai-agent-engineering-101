"""The GPT Pro profit review applied (review/W-20261103-2120/gpt-pro-answer1.md): which unit a buyer can be offered, the
life of a unit's lock, the floor and first ask, the restock guard over held + waiting + new stock, the next-window buy.

    python -m unittest test_profit_rules -v        (from submissions/26622007/week-08)
"""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import hard_limits
import profit_price
import restock
import shop_stock
from shop_stock import StockBook, needs

HERE = os.path.dirname(os.path.abspath(__file__))


def unit(uid, sku="G1", hours=500.0, grade="A", kind="food", cost=6000, list_price=10000, supplied_ago=3600):
    return {"unit": uid, "sku": sku, "name": f"포기김치 {sku}", "category": "식품>김치", "kind": kind, "cost": cost,
            "list_price": list_price, "grade": grade, "supplied_at": time.time() - supplied_ago,
            "expires_at": time.time() + hours * 3600 if kind == "food" else None, "cert": {"cert": {}, "sig": "s"}}



# scale 11 (W-20261110-1155-AXBook근본): restock breadth 7 SKUs, taken back out at the breadth-1110 max (11/11 01:41)
SCALE_11 = {"G53b150b5": 3, "Gf3e749a1": 2, "Gea0cca52": 2, "G2af689a3": 2, "Gbf98024f": 2, "Gfbf7f983": 3,
            "G28bd9142": 2}     # G57d8dfa6, G04ee8423, Gc44de87c net <= +0.011 of list at our 0.66 (independent review)
# breadth-1111 keep (review/W-20261111-1415-AXPro주기/verdict-breadth.md): its 10 arm-B SKUs at 2 after every older line.
# Listed inside the window, each with its own row in the verdict's per-SKU table (G04ee8423, Gbf98024f included: that
# measured result replaces the scale-11 estimate); arm A (Gea0cca52, Gc44de87c, ...) was never listed and stays out.
BREADTH_1111 = ["G04ee8423", "G6841e8a3", "G7aac0d6f", "Ga4e6abc9", "Gbf98024f", "G2d1fbb87", "G5b96e0fd", "G40e27aec",
                "Gfe4f052a", "Ge7edc329"]
# breadth-t2-1111 keep (same verdict, 17:12 point rule): its 8 arm-B SKUs at 2 after breadth-1111's; G53b150b5 and
# G28bd9142 of scale 11 come back on their own measured arm-B rows; arm A stays out.
BREADTH_T2_1111 = ["G53b150b5", "Ge4f8ef71", "G5c543233", "G0c4045d3", "G28bd9142", "G196023bf", "G326ebe9a", "G5e3784e6"]
BREADTH_ALL = BREADTH_1111 + BREADTH_T2_1111

class NeedsTest(unittest.TestCase):
    def test_reads_what_real_buyers_wrote(self):
        self.assertEqual(needs("I want to buy G200e39a3. Please quote this exact SKU, certificate and warranty hours. "
                               "Food must last at least 126.0 hours after payment.")["hours"], 126.0)
        self.assertEqual(needs("I want to buy 빼빼로 (Gfbb10ae2). I need at least 72.0 hours of shelf life.")["hours"], 72.0)
        self.assertEqual(needs("I want to buy 육개장 (G1234abcd). Please include the unit in the receipt. "
                               "I need it to last 48 hours.")["hours"], 48.0)
        self.assertEqual(needs("with market certificate (cert=true) and at least 96h shelf life")["hours"], 96.0)
        self.assertEqual(needs("I want to buy G1234abcd")["skus"], {"G1234abcd"})

    def test_warranty_hours_are_not_shelf_life_and_no_grade_is_not_a_grade(self):
        n = needs("I want to buy (G2) with a 6 h warranty. Quote price, grade and warranty.")
        self.assertIsNone(n["hours"])
        self.assertIsNone(n["grades"])
        self.assertEqual(needs("grade A or B only")["grades"], {"A", "B"})
        self.assertEqual(needs("grade b or better")["grades"], {"A", "B"})
        self.assertEqual(needs("B등급 이상")["grades"], {"A", "B"})
        self.assertIsNone(needs("Quote price, a grade and warranty")["grades"])

    def test_a_json_checkout_policy_is_read(self):
        # 11/06 09:26 task e6e6f95a: the words alone gave hours None, grades None, cert False (GPT Pro 07:30 S2)
        n = needs('I want to buy [더싱싱] 깍두기 2kg (G37da7a5c). Required checkout policy: '
                  '{"cert": true, "grades": ["A", "B"], "min_hours_left": 42.05}')
        self.assertEqual((n["hours"], n["grades"], n["cert"], n["skus"]), (42.05, {"A", "B"}, True, {"G37da7a5c"}))
        n = needs('I want to buy 앞치마 (G53e73f8c). Required checkout policy: {"cert": true, "grades": ["A", "B"], "warranty_hours": 6}')
        self.assertEqual((n["hours"], n["grades"], n["cert"]), (None, {"A", "B"}, True))     # warranty hours are not shelf life
        self.assertEqual(needs('policy: {"min_hours_left": 30} and at least 50 hours of shelf life')["hours"], 50.0)
        self.assertEqual(needs('{not json} grade A or B')["grades"], {"A", "B"})
        self.assertEqual(needs('{"grades": "c"}')["grades"], {"C"})
        self.assertFalse(needs('{"cert": false}')["cert"])
        self.assertIsNone(needs('{"min_hours_left": 1e400}')["hours"])
        self.assertEqual(needs('grade B or better. {"grades": ["B", "C"]}')["grades"], {"B", "C"})   # the policy wins


class EligibleFirstTest(unittest.TestCase):
    def test_a_unit_that_will_not_last_is_skipped_even_though_it_expires_first(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("short", hours=60), unit("long", hours=500)]})
        self.assertEqual(b.find("I want to buy (G1). I need at least 72 hours of shelf life.")["unit"], "long")
        self.assertEqual(b.find("I want to buy (G1)")["unit"], "short")              # no need named: first to expire

    def test_the_margin_to_pay_counts(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("edge", hours=72.05)]})
        u, why = b.pick("I want to buy (G1). It must last 72 hours.")
        self.assertIsNone(u)
        self.assertIn("72 h asked", why)

    def test_goods_offer_the_lowest_floor_unit_not_the_oldest(self):
        units = [unit("old-dear", kind="goods", cost=7400, supplied_ago=4 * 3600),
                 unit("new-cheap", kind="goods", cost=5900, supplied_ago=1800)]
        b = StockBook(lambda: {"round": 3, "units": units})
        self.assertEqual(b.find("I want to buy (G1)")["unit"], "old-dear")                     # no rank: the oldest
        u, _ = b.pick("I want to buy (G1)", rank=lambda x: x["cost"])
        self.assertEqual(u["unit"], "new-cheap")
        u, _ = b.pick("I want to buy (G1)", rank=lambda x: 1 / 0)                          # a broken rank: the oldest
        self.assertEqual(u["unit"], "old-dear")

    def test_food_still_goes_by_expiry_with_a_rank(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("short", hours=100, cost=9000), unit("long", hours=500, cost=1000)]})
        u, _ = b.pick("I want to buy (G1)", rank=lambda x: x["cost"])
        self.assertEqual(u["unit"], "short")

    def test_seller_floor_rank_puts_the_cheaper_unit_first_and_counts_the_grade(self):
        import seller
        pricing = {"price": {"goods_break": 0.08, "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344}}}
        with mock.patch.object(seller, "break_chance", lambda sku, grade=None: None):
            a_dear = seller.floor_rank(unit("a1", kind="goods", cost=2828629, list_price=3816000), pricing)
            a_cheap = seller.floor_rank(unit("a2", kind="goods", cost=2450000, list_price=3816000), pricing)
            c_cheap = seller.floor_rank(unit("c1", kind="goods", grade="C", cost=2450000, list_price=3816000), pricing)
        self.assertEqual(a_dear, 3206719)                       # the floor the 21:11 log line shows for u-adbd67d153e8
        self.assertLess(a_cheap, a_dear)
        self.assertGreater(c_cheap, a_cheap)                    # C breaks 34%: its floor is higher at the same cost
        with mock.patch.object(seller, "break_chance", lambda sku, grade=None: 0.6):
            self.assertEqual(seller.floor_rank(unit("x", kind="goods", cost=1, list_price=3816000),
                                               {**pricing, "warranty": {"max_break": 0.5}}), float("inf"))

    def test_grade_and_named_sku(self):
        b = StockBook(lambda: {"round": 3, "units": [unit("c", grade="C"), unit("a", sku="G2", grade="A")]})
        self.assertIsNone(b.find("I want to buy (G1), grade A or B"))
        self.assertEqual(b.find("I want to buy (G2), grade A")["unit"], "a")


class LockTest(unittest.TestCase):
    def setUp(self):
        self.units = [unit("a"), unit("b", hours=600)]
        self.paid, self.unpaid = [], []
        self.b = StockBook(lambda: {"round": 3, "units": list(self.units)})
        self.b.on_paid = self.paid.append
        self.b.on_unpaid = lambda info, why: self.unpaid.append((info["task"], why))

    def test_a_paid_unit_leaves_the_stock_and_is_reported_paid(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.assertEqual(self.b.find("G1")["unit"], "b")
        self.units.pop(0)                                       # the kit's checkout settled: inventory.json lost it
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t1"])
        self.assertIn("a", self.b.sold)

    def test_an_unpaid_deal_frees_its_unit_after_the_wait(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.assertEqual(self.unpaid[0][0], "t1")
        self.assertEqual(self.paid, [])

    def test_a_payment_in_progress_keeps_the_unit(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS - 5
        self.b.hold("a")                                        # our checkout's complete renews the hold
        self.b.agreed["a"]["at"] -= 1000
        self.assertEqual(self.b.find("G1")["unit"], "b")
        self.assertEqual(self.unpaid, [])

    def test_a_cancelled_or_refused_checkout_frees_the_unit_at_once(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.release("a")
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.assertEqual(self.unpaid, [("t1", "checkout cancelled or refused")])

    def test_food_that_expired_while_waiting_is_not_a_sale(self):
        self.units[0] = dict(self.units[0], expires_at=time.time() + 1)
        self.b.agree("a", {"task": "t1", "price": 9000, "expires_at": time.time() - 1})
        self.units.pop(0)
        self.b.units()
        self.assertEqual(self.paid, [])
        self.assertEqual(self.unpaid[0][1], "expired before payment")

    def test_a_unit_sold_while_a_talk_was_silent_is_not_promised_again(self):
        self.b.hold("a", "t1")
        self.b.held["a"] -= shop_stock.HOLD_SECONDS               # t1 went quiet
        self.assertEqual(self.b.find("G1")["unit"], "a")
        self.b.hold("a", "t2")
        self.b.agree("a", {"task": "t2", "price": 9000})
        self.units.pop(0)                                       # t2 paid
        self.assertFalse(self.b.available("a", "t1"))
        self.assertEqual([p["task"] for p in self.paid], ["t2"])
        self.assertTrue(self.b.available("b", "t1"))

    def test_a_payment_after_the_wait_is_still_credited_in_kit_mode(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.assertEqual(self.b.find("G1")["unit"], "a")          # on sale again
        self.units.pop(0)                                       # the slow buyer paid
        self.b.units()
        self.assertEqual([(p["task"], p["late"]) for p in self.paid], [("t1", True)])

    def test_a_newer_deal_on_the_unit_wins_over_the_late_one(self):
        self.b.agree("a", {"task": "t1", "price": 9000})
        self.b.held["a"] -= shop_stock.PAY_SECONDS + 1
        self.b.units()
        self.b.agree("a", {"task": "t2", "price": 9500})
        self.units.pop(0)
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t2"])

    def test_paid_food_checked_after_its_date_is_still_a_sale(self):
        self.units[0] = dict(self.units[0], expires_at=time.time() + 100)
        self.b.agree("a", {"task": "t1", "price": 9000, "expires_at": time.time() + 100})
        self.b.units()                                          # seen in stock, still good
        self.units.pop(0)
        self.b.agreed["a"].update(seen=time.time() - 200, expires_at=time.time() - 100)   # last seen good, looked at after its date
        self.b.units()
        self.assertEqual([p["task"] for p in self.paid], ["t1"])

    def test_a_silent_talk_lets_go_sooner_than_before(self):
        self.assertLessEqual(shop_stock.HOLD_SECONDS, 300)
        self.b.hold("a", "t1")
        self.assertEqual(self.b.holder("a"), "t1")
        self.b.held["a"] -= shop_stock.HOLD_SECONDS
        self.assertIsNone(self.b.holder("a"))


class GradeProfitDefaultTest(unittest.TestCase):
    NOW = 1794490000

    def cfg(self, **changes):
        return {"grade_profit_share": {"A": 0.08}, "grade_profit_hours": 6,
                "grade_profit_until": self.NOW - 1, **changes}

    def test_default_true_bypasses_only_the_until_window(self):
        for until in (self.NOW - 1, None, self.NOW + 13 * 3600):
            cfg = self.cfg(grade_profit_default=True, grade_profit_until=until)
            if until is None:
                del cfg["grade_profit_until"]
            with self.subTest(until=until):
                self.assertEqual(profit_price.grade_profit_share(cfg, "goods", "A", now=self.NOW), 0.08)

    def test_only_boolean_true_enables_the_default(self):
        for value in (None, False, 1, "true"):
            cfg = self.cfg(grade_profit_default=value)
            with self.subTest(value=value):
                self.assertEqual(profit_price.grade_profit_share(cfg, "goods", "A", now=self.NOW), 0.0)
        self.assertEqual(profit_price.grade_profit_share(self.cfg(), "goods", "A", now=self.NOW), 0.0)

    def test_default_keeps_kind_grade_stock_and_share_guards(self):
        fresh = {"sku": "G1", "grade": "A", "supplied_at": self.NOW}
        cases = [("food", "A", fresh, {}), ("goods", "B", fresh, {}), ("goods", "C", fresh, {}),
                 ("goods", "A", fresh, {"scarce_asks": {"G1": 9000}}),
                 ("goods", "A", fresh, {"ask_caps": {"G1": {"A": 9000}}}),
                 ("goods", "A", {**fresh, "supplied_at": self.NOW - 6 * 3600}, {}),
                 ("goods", "A", fresh, {"grade_profit_share": {"A": 0.2}})]
        for kind, grade, u, changes in cases:
            with self.subTest(kind=kind, grade=grade, unit=u, changes=changes):
                self.assertEqual(profit_price.grade_profit_share(
                    self.cfg(grade_profit_default=True, **changes), kind, grade, u, now=self.NOW), 0.0)

    def test_default_still_rejects_an_invalid_clock(self):
        for now in (float("nan"), float("inf"), "invalid", True):
            with self.subTest(now=now):
                self.assertEqual(profit_price.grade_profit_share(
                    self.cfg(grade_profit_default=True), "goods", "A", now=now), 0.0)

    def test_shipped_default_survives_until_for_a_new_a_goods_unit(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            cfg = json.load(f)["price"]
        self.assertGreater(self.NOW, cfg["grade_profit_until"])
        self.assertEqual(profit_price.grade_profit_share(
            cfg, "goods", "A", {"sku": "new-sku", "grade": "A", "supplied_at": self.NOW}, now=self.NOW), 0.08)

    def test_quote_default_adds_eight_percent_of_list_after_expected_refund(self):
        u = {"kind": "goods", "grade": "A", "sku": "G1", "cost": 50000, "list_price": 100000,
             "supplied_at": self.NOW}
        cfg = self.cfg(grade_profit_default=True, min_profit=0, min_profit_share=0, storage_share=0,
                       goods_break=0.0, grade_break={"A": 0.0})
        with mock.patch.object(profit_price.time, "time", return_value=self.NOW):
            baseline = profit_price.quote(u, pricing={"price": {**cfg, "grade_profit_default": False}})
            active = profit_price.quote(u, pricing={"price": cfg})
        self.assertEqual(baseline["floor"], 50000)
        self.assertEqual(active["floor"], 58000)


class PriceTest(unittest.TestCase):
    def test_floor_matches_the_review(self):
        stand = {"kind": "goods", "cost": 8518, "list_price": 11900}
        self.assertEqual(profit_price.floor(8518, 11900, "goods", 0.15), 11223)     # §2-b table
        self.assertEqual(profit_price.floor(8518, 11900, "goods", 0.25), 12720)
        self.assertEqual(profit_price.floor(6000, 10000, "food", 0.15), 7015)      # food: b = 0 whatever is passed
        q = profit_price.quote(stand)
        self.assertEqual((q["floor"], q["stress_floor"], q["refuse"]), (11223, 12720, False))

    def test_first_ask_is_105_percent_of_the_median_capped_at_list_and_never_below_the_floor(self):
        self.assertEqual(profit_price.opening(7000, 10000, median=8000)[0], 8400)
        self.assertEqual(profit_price.opening(7000, 10000, median=9800)[0], 10000)
        self.assertEqual(profit_price.opening(9000, 10000, median=7000)[0], 9000)
        self.assertEqual(profit_price.opening(7000, 10000, kind="food")[0], 7434)            # no median: 70.8% of list
        self.assertEqual(profit_price.opening(7000, 10000, median=8000, paid=[9500])[0], 9500)  # a price we were paid stays

    def test_scarce_ask_is_a_fixed_candidate_that_does_not_climb_with_the_median(self):
        pricing = {"price": {"scarce_asks": {"M": 262996}}}
        m = {"sku": "M", "kind": "goods", "cost": 150000, "list_price": 265050}
        other = {**m, "sku": "N"}
        self.assertEqual(profit_price.quote(other, median=238772, pricing=pricing)["open"], 250711)
        q = profit_price.quote(m, median=238772, pricing=pricing)
        self.assertEqual((q["open"], q["why"]), (262996, "scarce ask 262996"))
        self.assertEqual(profit_price.quote(m, median=245000, pricing=pricing)["open"], 262996)    # a higher median: same ask
        self.assertEqual(profit_price.quote(m, median=252500, pricing=pricing)["open"], 265050)    # median above it wins, list caps
        self.assertEqual(profit_price.quote(m, median=200000, paid=[264000], pricing=pricing)["open"], 264000)
        self.assertEqual(profit_price.quote(m, pricing=pricing)["open"], 262996)                   # no median: kind ratio path
        high = {"price": {"scarce_asks": {"M": 900000}}}
        self.assertEqual(profit_price.quote(m, median=238772, pricing=high)["open"], 265050)       # never above list
        for bad in (None, "x", True, -5, [1]):
            self.assertEqual(profit_price.quote(m, median=238772, pricing={"price": {"scarce_asks": {"M": bad}}})["open"], 250711)
        self.assertEqual(profit_price.quote(m, median=238772, pricing={"price": {"scarce_asks": [1]}})["open"], 250711)
        held = {**m, "cost": 260000}                                         # floor above the scarce ask: floor wins
        self.assertEqual(profit_price.quote(held, median=238772, pricing=pricing)["open"], profit_price.quote(held)["floor"])

    def test_ask_cap_lowers_the_first_ask_per_grade_but_never_under_the_floor(self):
        caps = {"price": {"scarce_asks": {"S": 392986}, "ask_caps": {"S": {"A": 350000, "C": 279000}, "W": {"*": 80000}}}}
        c = {"sku": "S", "kind": "goods", "grade": "C", "cost": 131904, "list_price": 420000}
        a = {**c, "grade": "A", "cost": 273599}
        q = profit_price.quote(c, median=330000, pricing=caps)                 # the scarce ask 392,986 loses to the cap
        self.assertEqual((q["open"], q["floor"]), (279000, 170393))
        self.assertTrue(q["why"].startswith("ask cap 279000 for grade C"))
        self.assertEqual(profit_price.quote(a, median=330000, pricing=caps)["open"], 350000)
        self.assertEqual(profit_price.quote({**a, "grade": "B"}, median=330000, pricing=caps)["open"], 392986)  # no B cap
        w = {"sku": "W", "kind": "goods", "grade": "A", "cost": 72637, "list_price": 96900}
        self.assertEqual(profit_price.quote(w, median=86555, pricing=caps)["open"], 89089)   # floor 89,089 above the cap
        self.assertEqual(profit_price.quote({**w, "cost": 55025}, median=86555, pricing=caps)["open"], 80000)
        low = {"price": {"ask_caps": {"W": {"*": 99000}}}}                     # a cap above the ask changes nothing
        self.assertEqual(profit_price.quote(w, median=86555, pricing=low)["open"], profit_price.quote(w, median=86555)["open"])
        for bad in (None, "x", True, -5, 0, [1], float("nan"), float("inf")):
            self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": {"W": {"*": bad}}}})["open"], 90883)
        self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": [1]}})["open"], 90883)
        self.assertEqual(profit_price.quote(w, median=86555, pricing={"price": {"ask_caps": {"W": 5}}})["open"], 90883)

    def test_goods_above_the_warranty_limit_are_refused_and_the_ledger_rate_counts(self):
        g = {"kind": "goods", "cost": 6000, "list_price": 10000}
        self.assertTrue(profit_price.quote(g, measured=0.3)["refuse"])
        self.assertEqual(profit_price.quote(g, measured=0.2)["b"], 0.2)
        self.assertEqual(profit_price.quote(g, measured=0.05)["b"], 0.15)

    def test_with_a_median_our_own_paid_price_no_longer_leads_when_the_setting_says_so(self):
        off = {"paid_over_median": False}
        self.assertEqual(profit_price.opening(7000, 10000, median=8000, paid=[9500], cfg={**profit_price.DEFAULTS, **off})[0], 8400)
        self.assertEqual(profit_price.opening(7000, 10000, paid=[9500], kind="goods", cfg={**profit_price.DEFAULTS, **off})[0], 9500)
        u = {"sku": "G8ec51935", "kind": "goods", "cost": 736782, "list_price": 2320000}
        q = profit_price.quote(u, median=0.757 * 2320000, paid=[2209874], pricing={"price": off})
        self.assertEqual(q["open"], round(1.05 * 0.757 * 2320000))     # was 2,209,874 (0.953 of list) at 17:11 on 11/06
        self.assertEqual(profit_price.quote(u, median=0.757 * 2320000, paid=[2209874])["open"], 2209874)  # default unchanged

    def test_grade_break_is_a_floor_when_the_ledger_rate_is_missing_or_thin(self):
        cfg = {"price": {"goods_break": 0.08, "grade_break": {"A": 0.03, "B": 0.122, "C": 0.344}}, "warranty": {"max_break": 0.5}}
        c = {"sku": "S", "kind": "goods", "grade": "C", "cost": 305000, "list_price": 1000000}
        self.assertEqual(profit_price.quote(c, pricing=cfg)["b"], 0.344)                    # no stats: not the 8% prior
        self.assertEqual(profit_price.quote(c, measured=0.09, pricing=cfg)["b"], 0.344)     # a SKU's mixed rate: still C's
        self.assertEqual(profit_price.quote(c, measured=0.4, pricing=cfg)["b"], 0.4)
        a = {**c, "grade": "A"}
        self.assertEqual(profit_price.quote(a, pricing=cfg)["b"], 0.08)
        self.assertEqual(profit_price.quote({**c, "grade": None}, pricing=cfg)["b"], 0.15)  # no rate, no grade: old prior
        self.assertEqual(profit_price.quote({**c, "grade": None}, measured=0.05, pricing=cfg)["b"], 0.08)
        q = profit_price.quote(c, pricing=cfg)
        self.assertGreaterEqual(q["floor"] * (1 - 0.344), 305000)                          # expected take covers the cost
        self.assertIn("floor at b 34%", profit_price.line({**c, "unit": "u"}, q))

    def test_stress_floor_is_never_under_the_units_own_break_chance(self):
        g = {"kind": "goods", "cost": 6000, "list_price": 10000}
        q = profit_price.quote(g, measured=0.34, pricing={"warranty": {"max_break": 0.45}})
        self.assertEqual(q["stress_floor"], q["floor"])
        self.assertFalse(q["refuse"])
        self.assertTrue(profit_price.quote(g, measured=0.5, pricing={"warranty": {"max_break": 0.45}})["refuse"])

    def test_a_dear_a_unit_bought_near_the_median_opens_under_it_and_never_under_break_even(self):
        # 11/06 20:30-22:30: 76 of 96 lost 100만+ quotes opened at a floor over the median (cost 0.684 of list against a
        # 0.694 median); at the median 42% of 30만+ quotes were paid, over 1.10 x median 13%
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            p = json.load(f)
        u = {"kind": "goods", "grade": "A", "cost": 1980000, "list_price": 3000000}
        be = {**p, "price": {**p["price"], "grade_profit_share": {}}}            # break-even floors, as shipped to 11/10
        q = profit_price.quote(u, median=2070000, measured=None, pricing=be)
        self.assertLessEqual(q["open"], 2070000)
        self.assertGreaterEqual(q["floor"], 1980000 * 1.0025 / (1 - 0.03))      # cost, storage and the A refund rate
        self.assertEqual(q["open"], max(q["floor"], round(0.97 * 2070000)))
        cheap = {**u, "cost": 1500000}
        self.assertEqual(profit_price.quote(cheap, median=2070000, pricing=be)["open"], round(0.97 * 2070000))
        # shipped 11/11 until price.grade_profit_until (grade A's 0.08): the dear draw's floor keeps 8% of list
        # after the refund chance, (1,980,000 + 4,950 + 240,000) / 0.965 = 2,305,648, over the median: it opens there
        # and waits (arm B sold 78% of its grade A units against 87%, and still kept +0.062 of list a unit against -0.007)
        until = p["price"]["grade_profit_until"]
        with mock.patch.object(profit_price.time, "time", return_value=until - 3600):
            q = profit_price.quote(u, median=2070000, measured=None, pricing=p)
            self.assertEqual((q["floor"], q["open"]), (2305648, 2305648))
            # the cheap draw's floor (1,806,995) is under 0.97 x the median: the first ask is the market's, as before
            q = profit_price.quote(cheap, median=2070000, pricing=p)
            self.assertEqual((q["floor"], q["open"]), (1806995, round(0.97 * 2070000)))
        p = {**p, "price": {**p["price"], "grade_profit_default": False}}
        with mock.patch.object(profit_price.time, "time", return_value=until):     # default off: expiry restores break-even
            self.assertEqual(profit_price.quote(u, median=2070000, pricing=p), profit_price.quote(u, median=2070000, pricing=be))

    def test_selling_food_comes_first_and_dear_skus_we_do_not_sell_are_left_out(self):
        # 메인 11/07 01:25 (가게 협상 점검 4번): Gb2392fa9 was cut 106 times by "cash kept" behind the dear lines while it
        # sold out 52 times; since 01:07 G6cd0e9d4 sold 1 of the market's 45 (ours 1,930,007 vs 1,781,496) and G8adbc159
        # 1 of 15, while G4a12722c (3 of 35) and G00327e80 (3 of 18) sell at the market's median and stay
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            r = json.load(f)["restock"]
        self.assertEqual(r["priority_skus"][:3], ["Gef4d3036", "Gb2392fa9", "G200e39a3"])
        self.assertNotIn("G6cd0e9d4", r["priority_skus"] + r["high_price_proven_skus"])
        self.assertNotIn("G8adbc159", r["priority_skus"] + r["high_price_proven_skus"])
        self.assertIn("G4a12722c", r["priority_skus"])

    def test_food_stock_cap_leaves_room_for_listed_fast_food(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            cfg = json.load(f)["restock"]
        skus = [f"FAST_FOOD_{i}" for i in range(8)]
        cfg = {**cfg, "priority_skus": skus}
        catalog = {sku: {"kind": "food", "list_price": 23000} for sku in skus}
        buy = [{"sku": sku, "qty": 20} for sku in skus]
        inv = {"balance": 200000000, "units": [
            {"sku": "HELD_FOOD", "kind": "food", "cost": 2500000}
        ]}
        self.assertEqual(restock.guard(buy, inv, catalog, cfg), buy)

        held = cfg["food_max_stock_cost"] - 100000
        inv = {**inv, "units": [{"sku": "HELD_FOOD", "kind": "food", "cost": held}]}
        kept = restock.guard(buy, inv, catalog, cfg)
        self.assertNotEqual(kept, buy)
        self.assertTrue(kept)  # the remaining room still admits whole units
        self.assertLessEqual(sum(line["qty"] * 0.75 * 23000 for line in kept),
                             100000)

    def test_idle_cash_goes_to_the_markets_top_unlisted_dear_skus(self):
        # main 11/06 23:25: proceeds back into stock; dear goods were short of quote requests, not units, so breadth
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            r = json.load(f)["restock"]
        new = ["Ge5fe0d41", "G19a70f63", "G0667d440", "G85a1a856", "G9f7c7d1b", "Gd289350f", "G6b0fd5bc", "G2c023a8c",
               "G7978c65e", "G9357d6c2"]
        after = r["priority_skus"].index("G75f11c19") + 1
        self.assertEqual(r["priority_skus"][after:after + 10], new)      # before the last food line (independent review)
        for sku in new:
            self.assertGreaterEqual(r["sku_target_units"][sku], 2)
        self.assertEqual((r["food_max_stock_cost"], r["food_max_qty"], r["sku_target_units"]["G75f11c19"]), (8000000, 20, 8))

    def test_cash_over_the_buffer_goes_to_two_more_unlisted_dear_skus_last(self):
        # main 11/07 05:5x: ~40M over the cash buffer; 30만+ SKUs with a positive net at our 0.685 cost in 6 h and 24 h
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            r = json.load(f)["restock"]
        new = {"G0e4deffc": 1, "G147ae4e7": 3}      # G0e4deffc 1: its median is one buyer at one shop (independent review)
        i = r["priority_skus"].index("G0e4deffc")
        self.assertEqual(r["priority_skus"][i:i + 2], list(new))  # after the food and every older line (243a8f7 order)
        self.assertEqual(r["priority_skus"][i + 2:], BREADTH_ALL)   # scale 11 taken back out: only the breadth studies' after them
        self.assertEqual(r["priority_skus"][:3], ["Gef4d3036", "Gb2392fa9", "G200e39a3"])
        self.assertEqual({k: r["sku_target_units"][k] for k in new}, new)
        self.assertFalse(set(new) & set(r["skip_skus"]))

    def test_breadth_scale_11_taken_back_out(self):
        # W-20261110-1155-AXBook근본: the 7 breadth SKUs (11/10 13:41) leave priority_skus and sku_target_units at the
        # breadth-1110 max; units already held stay on sale, no other SKU or target changes
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            r = json.load(f)["restock"]
        back = set(SCALE_11) & (set(r["priority_skus"]) | set(r["sku_target_units"]))
        self.assertEqual(back, {"Gbf98024f", "G53b150b5", "G28bd9142"})   # breadth studies' arm B, own measured rows; A stays out
        self.assertEqual({k: r["sku_target_units"][k] for k in back}, {k: 2 for k in back})
        self.assertEqual(r["priority_skus"][-len(BREADTH_ALL) - 2:-len(BREADTH_ALL)], ["G0e4deffc", "G147ae4e7"])
        self.assertIn("G07ec17d5", r["skip_skus"])
        self.assertFalse({"G57d8dfa6", "Gc44de87c"} & (set(r["priority_skus"]) | set(r["sku_target_units"])))
        self.assertEqual([k for k in r["priority_skus"] if k == "G04ee8423"], ["G04ee8423"])   # breadth-1111 arm B only
        self.assertEqual(len(set(r["priority_skus"])), len(r["priority_skus"]))
        self.assertEqual(r["sku_target_units"]["G2c023a8c"], 3)      # Restore after g2-loss-target-1110

    @mock.patch.object(profit_price.time, "time", return_value=1794350000.0)
    def test_a_grades_profit_share_lifts_only_that_grades_goods_floor(self, _clock):
        # goods-margin-1110's arm B as a grade's default: floor = (C + 0.0025 C + share x L) / (1 - b)
        base = {"price": {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035,
                          "grade_break": {"A": 0.03, "B": 0.15, "C": 0.344}, "grade_profit_until": 1794350000.0 + 3600}}
        on = {"price": {**base["price"], "grade_profit_share": {"B": 0.08}}}
        # on only with an end that has not come: no end, a bad one or a past one is break-even (no open-ended default)
        for until in (None, "x", True, float("nan"), 1.0):
            ended = {"price": {**on["price"], "grade_profit_until": until}}
            self.assertEqual(profit_price.quote({"sku": "G", "unit": "u", "kind": "goods", "grade": "B", "cost": 500000,
                                                 "list_price": 922000}, pricing=ended)["floor"], 589708, until)
        no_key = {"price": {k: v for k, v in on["price"].items() if k != "grade_profit_until"}}
        self.assertEqual(profit_price.quote({"sku": "G", "unit": "u", "kind": "goods", "grade": "B", "cost": 500000,
                                             "list_price": 922000}, pricing=no_key)["floor"], 589708)
        b_unit = {"sku": "G85a1a856", "unit": "u", "kind": "goods", "grade": "B", "cost": 500000, "list_price": 922000}
        self.assertEqual(profit_price.quote(b_unit, pricing=base)["floor"], 589708)             # (500,000 + 1,250 + 1) / 0.85
        # (500,000 + 1,250 + 73,760) / 0.85 = 676,482.4 -> 676,483: after a 15% refund chance 73,760 = 8% of list is kept
        q = profit_price.quote(b_unit, pricing=on)
        self.assertEqual(q["floor"], 676483)
        self.assertAlmostEqual((q["floor"] * 0.85 - 500000 * 1.0025) / 922000, 0.08, places=4)
        self.assertGreaterEqual(q["open"], q["floor"])
        for other in ({**b_unit, "grade": "A"}, {**b_unit, "grade": "C"}, {**b_unit, "grade": None},
                      {**b_unit, "kind": "food", "expires_at": None}):
            self.assertEqual(profit_price.quote(other, pricing=on)["floor"], profit_price.quote(other, pricing=base)["floor"], other)
        for bad in (0, -0.1, 0.2, True, "0.08", None, float("nan")):                               # not a share: today's floor
            p = {"price": {**base["price"], "grade_profit_share": {"B": bad}}}
            self.assertEqual(profit_price.quote(b_unit, pricing=p)["floor"], 589708, bad)
        self.assertEqual(profit_price.quote(b_unit, pricing={"price": {**base["price"], "grade_profit_share": [0.08]}})["floor"], 589708)
        dear = {**b_unit, "cost": 760000}                    # break-even 896,354; + 8% would be 983,130, over the 922,000 list
        self.assertEqual(profit_price.quote(dear, pricing=base)["floor"], 896355)
        self.assertEqual(profit_price.quote(dear, pricing=on)["floor"], 922000)
        over = {**b_unit, "cost": 800000}                    # break-even already over list: it stays, nothing is lowered
        self.assertEqual(profit_price.quote(over, pricing=on)["floor"], profit_price.quote(over, pricing=base)["floor"])
        self.assertEqual(profit_price.grade_profit_share({"grade_profit_share": {"B": 0.15}, "grade_profit_until": 1794350000.0 + 3600},
                                                         "goods", "B"), 0.15)

    def test_grade_profit_end_must_be_within_twelve_hours(self):
        now = 1794350000.0
        for seconds, expected in ((12 * 3600 + 1, 0.0), (12 * 3600, 0.08),
                                  (10 ** 11 - now, 0.0), (1794393000000 - now, 0.0)):
            cfg = {"grade_profit_share": {"A": 0.08}, "grade_profit_until": now + seconds}
            with self.subTest(seconds=seconds):
                self.assertEqual(profit_price.grade_profit_share(cfg, "goods", "A", now=now), expected)

    def test_the_share_is_not_for_a_capped_or_scarce_unit_nor_for_one_that_has_waited(self):
        # independent review 9d5c7b1 I1: 12 of the 91 ask-capped units held would have got a floor over their cap
        price = {"min_profit": 1, "min_profit_share": 0.0, "goods_break": 0.035, "grade_break": {"A": 0.03, "B": 0.15},
                 "grade_profit_share": {"A": 0.08, "B": 0.08}, "grade_profit_hours": 6, "grade_profit_until": 1794350000.0 + 3600}
        now = 1794350000.0
        u = {"sku": "G1", "unit": "u", "kind": "goods", "grade": "A", "cost": 575000, "list_price": 1000000, "supplied_at": now - 600}
        with mock.patch.object(profit_price.time, "time", return_value=now):
            self.assertEqual(profit_price.quote(u, pricing={"price": price})["floor"], 680247)
            capped = {"price": {**price, "ask_caps": {"G1": {"A": 650000}}}}
            q = profit_price.quote(u, median=700000, pricing=capped)
            self.assertEqual((q["floor"], q["open"]), (597346, 650000))                 # the cap still caps: break-even under it
            self.assertEqual(profit_price.quote({**u, "grade": "B", "cost": 450000}, pricing=capped)["floor"],
                             math.ceil((450000 + 1125 + 80000) / 0.85))                 # the cap is grade A's only
            scarce = {"price": {**price, "scarce_asks": {"G1": 900000}}}
            self.assertEqual(profit_price.quote(u, pricing=scarce)["floor"], 597346)
            # 대표님 11/11 00:26: what has waited goes at cost + expected refund, never under it
            self.assertEqual(profit_price.quote({**u, "supplied_at": now - 6 * 3600 + 1}, pricing={"price": price})["floor"], 680247)
            for waited in (now - 6 * 3600, now - 80 * 3600):
                self.assertEqual(profit_price.quote({**u, "supplied_at": waited}, pricing={"price": price})["floor"], 597346)
            for unknown in (None, "x", float("nan"), True):                                 # no arrival time: a new unit
                self.assertEqual(profit_price.quote({**u, "supplied_at": unknown}, pricing={"price": price})["floor"], 680247)
            for off in (None, 0, -1, "6", True):                                            # no hours: the share for every age
                p = {"price": {**price, "grade_profit_hours": off}}
                self.assertEqual(profit_price.quote({**u, "supplied_at": now - 80 * 3600}, pricing=p)["floor"], 680247, off)
        self.assertEqual(profit_price.grade_profit_share(price, "goods", "A", u, now=now), 0.08)
        self.assertEqual(profit_price.grade_profit_share(price, "goods", "A", {**u, "supplied_at": now - 7 * 3600}, now=now), 0.0)

    def test_shipped_settings(self):
        with open(os.path.join(HERE, "pricing.json"), encoding="utf-8") as f:
            p = json.load(f)
        self.assertEqual(p["concession"], [0.3, 0.2, 0.1])       # GPT Pro 11/06 16:45 §3.2: revenue first, nearer the median
        c = profit_price.cfg_of(p)
        # a person 11/06 22:22: floors at break-even (cost + 6 h storage + the expected warranty refund), no profit margin:
        # the least b is grade A's measured 3% plus half a point, and no minimum profit
        self.assertEqual((c["goods_break"], c["min_profit"], c["min_profit_share"]), (0.035, 1, 0.0))
        # Latest instruction: keep 30만+ goods at 0.97 x median with the price trial OFF.
        self.assertEqual((c["median_up"], c["high_median_up"]["factor"]), (1.0, 0.97))
        self.assertIs(profit_price.cfg_of(p)["paid_over_median"], False)
        # B 0.122 -> 0.15 (11/10): our own grade B breaks 14.3% over the round, 15.2% over the last 72 h (market 11.6-11.9%)
        # C 0.344 -> 0.42 (11/16, W-20261116-1255-AX재고상한기본): our own grade C broke 53 of 127 (41.7%) over the 3 days
        # to 11/16 01:00 and 39 of 88 (44.3%) over the last day, the other shops 34.8-35.1%; at 0.344 the floor sold C at
        # break-even for the market's rate, so our C sales lost 3.57M (refunds 31.0M of 66.2M on 11% of goods sales)
        self.assertEqual(profit_price.cfg_of(p)["grade_break"], {"A": 0.03, "B": 0.15, "C": 0.42})
        b_unit = {"sku": "G85a1a856", "unit": "u", "kind": "goods", "grade": "B", "cost": 500000, "list_price": 922000}
        # W-20261112-0815-AXbook레버: grade A's proven 8% becomes the default; B and C stay at break-even.
        self.assertEqual(profit_price.cfg_of(p)["grade_profit_share"], {"A": 0.08})
        self.assertEqual(profit_price.cfg_of(p)["grade_profit_hours"], 6)
        until = profit_price.cfg_of(p)["grade_profit_until"]
        self.assertEqual(until, 1794481200)     # 11/12 20:00 KST: old seller bridge
        self.assertIs(profit_price.cfg_of(p)["grade_profit_default"], True)
        c_unit = {"sku": "G85a1a856", "unit": "u", "kind": "goods", "grade": "C", "cost": 350000, "list_price": 922000}
        a_unit = {"sku": "G03d0199f", "unit": "u", "kind": "goods", "grade": "A", "cost": 575000, "list_price": 1000000}
        with mock.patch.object(profit_price.time, "time", return_value=until - 3600):
            self.assertEqual(profit_price.quote(c_unit, median=None, measured=None, pricing=p)["floor"],
                             604959)     # (350,000 + 875 + 1) / 0.58 = 604,958.6 -> 604,959 (at 0.344: 534,872)
            # (500,000 + 1,250 + 1) / 0.85 = 589,707.06 -> 589,708 (at b 0.122: 570,900): grade B at break-even
            self.assertEqual(profit_price.quote(b_unit, median=None, measured=0.12, pricing=p)["floor"], 589708)
            # (575,000 + 1,437.5 + 80,000) / 0.965 = 680,246.1 -> 680,247 (break-even 597,346): 0.68 of list, what buyers pay
            self.assertEqual(profit_price.quote(a_unit, median=None, measured=None, pricing=p)["floor"], 680247)
        with mock.patch.object(profit_price.time, "time", return_value=until):
            self.assertEqual(profit_price.quote(a_unit, median=None, measured=None, pricing=p)["floor"], 680247)
            finite = {**p, "price": {**p["price"], "grade_profit_default": False}}
            self.assertEqual(profit_price.quote(a_unit, pricing=finite)["floor"], 597346)
        food = {"sku": "F", "unit": "u", "kind": "food", "grade": "A", "cost": 6000, "list_price": 10000}
        self.assertEqual(profit_price.quote(food, median=None, measured=None, pricing=p)["floor"], 6016)      # food: break-even
        self.assertEqual(profit_price.quote(b_unit, median=None, measured=0.2, pricing=p)["b"], 0.2)   # a higher measured rate wins
        self.assertEqual(p["warranty"]["max_break"], 0.5)         # C units (34% of them break) are priced, not refused
        r = p["restock"]
        self.assertEqual((r["max_stock_cost"], r["max_units_per_sku"], r["cover_hours"], r["keep_cash"],
                          r["food_max_stock_cost"], r["high_price_goods"], r["high_price_max_units"]),
                         (1000000000, 20, 6, 4500000, 8000000, 100000, 20))   # scale 9 (11/07 00:3x): food cap 650k -> 3M (food sold out 76 times, 0 expired); scale 8 (11/06 21:0x): 1B, hard_limits.cash_buffer limits buys; scale 7: units caps 10 -> 20, fill_listed fills each listed SKU to its target; scale 6: our 30만+ units sold 98% in 24 h, supply held revenue; each dear SKU stays at its own sku_target_units   # keep_cash: 21.9M open warranties at 05:00
        # 11/11 food cap: food cap 3M -> 8M: with fast food listed at 20 units the 3M cap shrank the listed food refills behind them (replay: 17 -> 20, 1 -> 3, 0 -> 3);
        # without such listing the first run changes by ~8 units and later runs of a window take unlisted food instead of unlisted goods;
        # need ~6M (held peak 2.25M + 5 SKUs x 20 at worst cost 1.7M + one window ~2M); cash checks unchanged.
        # scale 2 (a person's 15M cap, 11/05 11:48): cover 6 h and 3 dear units so the model asks for the 60만+ lines
        # (our share 0.35-0.76/h); six runs without a sale fill ~15.0M, 10.5M of it 60만+ (scale2-result.md)
        # food 65만: food sells in 0.1 h with nothing spoiled, and its 45만 cap cut 416 of the last 2,000 guard lines
        # (review/W-20261105-0953-AX패치3.3/scale-analysis.md)
        # 6 per SKU: G1515d951 147 of 147 sold, out of stock 45% of 11-04 10-16 h (review/W-20261104-1507 all-shops review)
        self.assertEqual(r["max_sku_share"], 0.2)
        proven = ["G1515d951", "G6b9db161"]           # each sold out before the next window (G1515d951 3 in 6 min, twice;
        # G6b9db161 2 per window 04:03-04:44); G0f415407 and G9c02e68e left extra units unsold twice: no more buying
        self.assertEqual((r["high_price_proven_max_units"], r["high_price_proven_skus"]), (20, proven))
        # G1515d951 at 6 also cut stock-outs 35% -> 0% while our sales went 41 -> 35 and the market's rose 921 -> 954
        # (13:25-16:46 vs 17:07-20:27), so 4 holds; the four foods out of stock 35-54% of 17:30-20:31 go to the front
        # (review/W-20261104-2030, GPT Pro 3판 §4.2). Under 15M the 60만+ lines go first: at 12:24 they came after five food
        # lines and G03d0199f was refused when the window closed (scale2-result.md §8)
        # scale 6 (11/06 17:2x): the 17 60만+ SKUs with the most expected net flow over 24 h of ledger rows go first, in
        # that order (review/W-20261106-1650-AX1등작전/analysis.md); our 30만+ units sold 98% with a 0.99 h median hold
        # scale 7 (11/06 19:1x, W-20261106-1900-AX자본투입): 7 more 30만+ SKUs (break 0-11 percent) before the food,
        # targets max(old, ceil(market sold/h x 0.10 x 2 h)) from the last 6 h of ledger supply/settle rows
        # scale 9 (11/07 00:3x): 10 more SKUs after these (test_idle_cash_goes_to_the_markets_top_unlisted_dear_skus);
        # G75f11c19 3 -> 8, G9c9f8f94 2 -> 4, G754bf0ca 5 -> 8, G8095ddd6 6 -> 8 (sold 2-4x their target in 6 h)
        old = [k for k in r["priority_skus"] if k not in ("Ge5fe0d41", "G19a70f63", "G0667d440", "G85a1a856", "G9f7c7d1b",
                                                           "Gd289350f", "G6b0fd5bc", "G2c023a8c", "G7978c65e", "G9357d6c2",
                                                           "G0e4deffc", "G147ae4e7") + tuple(SCALE_11)
               and k not in BREADTH_ALL]   # + scale 10, 11, breadth-1111, breadth-t2-1111
        self.assertEqual((old, {k: v for k, v in r["sku_target_units"].items() if k in old
                          or k in r["high_price_proven_skus"]}, r["fill_listed"], r["price_hours"]),
                         (["Gef4d3036", "Gb2392fa9", "G200e39a3", "G8ec51935", "G24f5f979", "Gbec2b68f", "Gf6b70da8", "Gb5d0dde7", "G2698c6f8", "G8095ddd6", "G021aae64", "G03d0199f", "G05277ffe", "G00327e80", "G3e0d6c67", "Gc1297f36", "G429bd69e", "G754bf0ca", "G47788edd", "G4a12722c", "G445ada94", "G2607d1f6", "G9c9f8f94", "G7a341425", "G9718023b", "G75f11c19", "G66ded25b", "G1465c678"],
                          {"G1515d951": 12, "G6b9db161": 2, "G75f11c19": 8, "G8ec51935": 12, "G24f5f979": 8, "Gbec2b68f": 4, "Gf6b70da8": 5, "Gb5d0dde7": 6, "G2698c6f8": 3, "G8095ddd6": 8, "G021aae64": 11, "G03d0199f": 6, "G05277ffe": 7, "G00327e80": 3, "G3e0d6c67": 2, "Gc1297f36": 4, "G429bd69e": 4, "G754bf0ca": 8, "G47788edd": 2, "G1465c678": 1, "G4a12722c": 3, "G7a341425": 2, "G445ada94": 3, "G9c9f8f94": 4, "G2607d1f6": 3, "G9718023b": 2}, True, 3))
        # scale 3 (12:24-13:05): G8ec51935 sold 2 in 40 min and was refilled each window -> 4; Gb5d0dde7's 3 units from
        # 12:27 sat unsold across the 12:41 and 13:01 windows -> 2 (scale2-result.md §8)   # G1515 4: Astra 21:19, same five units across two supply windows; G6 2: undo the reduction triggered by mislinked refunds
        # Patch 3.3 (book): one unit each of two 60만+ goods 12-14 shops sell 39-49/h at 3-4% mature break, after the proven
        # SKUs and food (scale-analysis.md); the 2.32M sofa G8ec51935 waits for a larger cap
        cat = {"G05277ffe": {"kind": "goods", "list_price": 600000}, "Gb5d0dde7": {"kind": "goods", "list_price": 1094400},
               "G8ec51935": {"kind": "goods", "list_price": 2320000}, "G8095ddd6": {"kind": "goods", "list_price": 851400}}
        cfg = hard_limits.clamp_cfg("restock", {**restock.DEFAULTS, **r})
        buy = [{"sku": s, "qty": 5} for s in cat]
        inv = {"balance": 50000000, "units": [{"sku": "G05277ffe", "kind": "goods", "cost": 360000}]}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(restock.guard(buy, inv, cat, cfg), [{"sku": "G05277ffe", "qty": 5}, {"sku": "Gb5d0dde7", "qty": 5},
                                                              {"sku": "G8ec51935", "qty": 5}, {"sku": "G8095ddd6", "qty": 5}])   # one held, target 7 (scale 7)
            self.assertEqual(restock.guard(buy, {**inv, "units": []}, cat, cfg),
                             [{"sku": "G05277ffe", "qty": 5}, {"sku": "Gb5d0dde7", "qty": 5}, {"sku": "G8ec51935", "qty": 5},
                              {"sku": "G8095ddd6", "qty": 5}])   # scale 6 lists G8095ddd6; each line is the asked 5 under its target
        self.assertEqual(r["food_max_qty"], 20)    # 11/07 04:4x: 20 (one line a sold-out food per window, ~10 s a line), scale 9: 10 (food sold out 76 times, 0 expired); was: every food line refills up to 4 units, not 3 (the SKU cap stays max_units_per_sku 6)
        self.assertTrue(r["high_price_listed_only"])
        # no cap per deal (hard_limits.UNCAPPED; at 800k my buyer settled 1 of 113 dear orders) and the whole list price:
        # 27 orders in 4.5 h ended with the shop's lowest ask between 0.9 and 1.0 of list, while only 6 of 271 deals paid
        # within 1% of the limit, so the limit is rarely where a deal lands
        self.assertNotIn("max_spend_per_deal", p["buyer"])
        self.assertEqual((p["buyer"]["max_price_share"], p["forum"]["every_minutes"]), (1.0, 10))
        self.assertTrue({"G054ff120", "G0f415407", "G9c02e68e", "Gf0293c0e", "G9c1a6eee", "Gbb0cbbff"} <= set(r["skip_skus"]))
        self.assertNotIn("G6b9db161", r["skip_skus"])   # held units sold at capped asks; allow replenishment
        self.assertEqual(p["price"]["scarce_asks"], {"G1515d951": 380911})
        self.assertEqual(p["price"]["ask_caps"]["G6b9db161"], {"A": 350000, "C": 279000})
        first = p["next_window"]["done_plans"][0]["targets"]          # the review's table: 10 SKUs, 13 units
        self.assertEqual((len(first), sum(first.values())), (10, 13))
        self.assertNotIn("G07ec17d5", first)
        self.assertEqual(p["next_window"]["targets"], {"G1465c678": 1})     # one-unit demand and mature-margin trial


CAT = {"F1": {"kind": "food", "list_price": 40000}, "F2": {"kind": "food", "list_price": 40000},
       "H1": {"kind": "goods", "list_price": 20000}, "BIG": {"kind": "goods", "list_price": 400000}}
CFG = {**restock.DEFAULTS, "keep_cash": 2500000, "max_stock_cost": 900000, "max_units_per_sku": 3, "food_max_qty": 3,
       "food_max_stock_cost": 200000, "high_price_goods": 100000, "high_price_max_units": 1, "skip_skus": ["X"]}


class GuardTest(unittest.TestCase):
    def held(self, sku, n, cost=10000, kind="goods"):
        return [{"unit": f"{sku}{i}", "sku": sku, "kind": kind, "cost": cost} for i in range(n)]

    def test_units_per_sku_count_stock_waiting_and_new(self):
        inv = {"balance": 5000000, "units": self.held("H1", 1)}
        waiting = [{"sku": "H1", "qty": 1, "worst": 15000}]
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 5}], inv, CAT, CFG, None, waiting), [{"sku": "H1", "qty": 1}])
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 2}, {"sku": "H1", "qty": 2}], inv, CAT, CFG), [{"sku": "H1", "qty": 2}])

    def test_one_unit_of_expensive_goods_and_skipped_skus(self):
        inv = {"balance": 5000000, "units": []}
        self.assertEqual(restock.guard([{"sku": "BIG", "qty": 2}, {"sku": "X", "qty": 1}], inv, CAT, CFG), [{"sku": "BIG", "qty": 1}])
        self.assertEqual(restock.guard([{"sku": "BIG", "qty": 1}], {"balance": 5000000, "units": self.held("BIG", 1)}, CAT, CFG), [])

    def test_two_units_of_expensive_goods_and_the_cut_is_logged(self):
        inv = {"balance": 5000000, "units": []}
        two = {**CFG, "high_price_max_units": 2}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 3}], inv, CAT, two), [{"sku": "BIG", "qty": 2}])
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 3}], inv, CAT, {**two, "max_stock_cost": 400000}),
                             [{"sku": "BIG", "qty": 1}])                    # 300,000 worst each
        self.assertIn("BIG 3 -> 2 (units per SKU)", err.getvalue())
        self.assertIn("BIG 3 -> 1 (units per SKU, stock cost)", err.getvalue())

    def test_an_empty_priority_food_cut_by_stock_cost_is_only_logged_for_the_reserve_trial(self):
        inv = {"balance": 5000000, "units": self.held("H1", 3, cost=295000)}       # 885,000 of 900,000 held
        cfg = {**CFG, "priority_skus": ["F1"]}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(restock.guard([{"sku": "F1", "qty": 2}, {"sku": "F2", "qty": 2}], inv, CAT, cfg), [])
        self.assertIn("food-reserve sim: F1 none held or waiting", err.getvalue())
        self.assertNotIn("food-reserve sim: F2", err.getvalue())                # not a priority food
        err = io.StringIO()
        with contextlib.redirect_stderr(err):           # one F1 already on the shelf: nothing to reserve
            restock.guard([{"sku": "F1", "qty": 2}], {"balance": 5000000, "units": inv["units"] + self.held("F1", 1, kind="food")}, CAT, cfg)
        self.assertNotIn("food-reserve sim", err.getvalue())

    def test_three_units_only_for_proven_expensive_goods(self):
        inv = {"balance": 5000000, "units": []}
        cat = {**CAT, "BIG2": {"kind": "goods", "list_price": 400000}}
        cfg = {**CFG, "max_stock_cost": 4000000, "high_price_max_units": 2, "high_price_proven_max_units": 3,
               "high_price_proven_skus": ["BIG"]}
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 4}, {"sku": "BIG2", "qty": 4}], inv, cat, cfg),
                             [{"sku": "BIG", "qty": 3}, {"sku": "BIG2", "qty": 2}])
            no_key = {k: v for k, v in cfg.items() if k != "high_price_proven_max_units"}   # falls back to the plain cap
            self.assertEqual(restock.guard([{"sku": "BIG", "qty": 4}], inv, cat, no_key), [{"sku": "BIG", "qty": 2}])

    def test_food_cost_cap_counts_what_is_held(self):
        inv = {"balance": 5000000, "units": self.held("F2", 3, cost=50000, kind="food")}       # 150,000 of food
        self.assertEqual(restock.guard([{"sku": "F1", "qty": 3}], inv, CAT, CFG), [{"sku": "F1", "qty": 1}])   # 30,000 worst each

    def test_stock_cost_cap_counts_waiting_lines(self):
        inv = {"balance": 5000000, "units": self.held("H1", 0)}
        waiting = [{"sku": "BIG", "qty": 1, "worst": 300000}, {"sku": "F2", "qty": 1, "worst": 590000}]
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], inv, CAT, CFG, None, waiting), [])

    def test_cash_kept(self):
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 3}], {"balance": 2520000, "units": []}, CAT, CFG), [{"sku": "H1", "qty": 1}])


class WithTrialTest(unittest.TestCase):
    def test_trial_goes_first_and_the_usual_lines_fill_the_room_left(self):
        inv = {"balance": 5000000, "units": []}
        # BIG's trial unit (worst 300,000) leaves 600,000 of the 900,000 stock cap: H1 3 still fits, BIG is not bought twice
        out = restock.with_trial([{"sku": "BIG", "qty": 1}], [{"sku": "BIG", "qty": 1}, {"sku": "H1", "qty": 3}],
                                 inv, CAT, CFG)
        self.assertEqual(out, [{"sku": "BIG", "qty": 1}, {"sku": "H1", "qty": 3}])
        self.assertEqual(restock.with_trial([{"sku": "BIG", "qty": 1}], [{"sku": "F2", "qty": 3}], inv, CAT,
                                            dict(CFG, max_stock_cost=320000)), [{"sku": "BIG", "qty": 1}])

    def test_no_trial_leaves_the_lines_alone(self):
        lines = [{"sku": "H1", "qty": 9}]
        self.assertEqual(restock.with_trial(None, lines, {}, CAT, CFG), lines)
        self.assertEqual(restock.with_trial([], lines, {}, CAT, CFG), lines)


class TrialInMainTest(unittest.TestCase):
    """restock.main() with a next_window trial: it joins the window's usual lines instead of replacing them."""
    CATALOG = {"H1": {"kind": "goods", "category": "a", "list_price": 20000},
               "H2": {"kind": "goods", "category": "b", "list_price": 20000}}
    PRICING = {"restock": {"keep_cash": 1500000, "max_stock_cost": 900000}, "next_window": {"id": "t", "targets": {"H1": 1}}}

    def run_main(self, trial, restock_state):
        with tempfile.TemporaryDirectory() as d:
            inv = {"round": 3, "balance": 5000000, "units": [], "restock": restock_state}
            for name, data in (("inventory.json", inv), ("catalog.json", self.CATALOG)):
                with open(os.path.join(d, name), "w") as f:
                    json.dump(data, f)
            printed, real_load = [], restock.load
            env = {k: v for k, v in os.environ.items() if k != "HARNESS"}
            with mock.patch.object(restock, "read_model", lambda *a: None), \
                    mock.patch.object(restock, "load", lambda p, dflt: self.PRICING if p == "pricing.json" else real_load(p, dflt)), \
                    mock.patch.object(restock.opening, "lines", lambda *a, **k: (None, "test")), \
                    mock.patch.object(restock, "next_window", lambda *a, **k: (trial, "trial")), \
                    mock.patch.object(restock, "plan", lambda *a, **k: {"buy": [{"sku": "H1", "qty": 2}, {"sku": "H2", "qty": 2}]}), \
                    mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["x", "--state", d]), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0] if a else "")):
                restock.main()
        return json.loads(printed[-1])["buy"]

    def test_the_trial_and_the_usual_lines_go_out_together(self):
        now = time.time()
        self.assertEqual(self.run_main([{"sku": "H1", "qty": 1}], {"open": True, "closes_at": now + 170}),
                         [{"sku": "H1", "qty": 1}, {"sku": "H2", "qty": 2}])

    def test_once_the_trial_is_sent_the_window_still_buys_but_not_its_skus(self):
        now = time.time()
        self.assertEqual(self.run_main([], {"open": True, "closes_at": now + 120}), [{"sku": "H2", "qty": 2}])
        self.assertEqual(self.run_main(None, {"open": True, "closes_at": now + 120}),     # no trial: all of the plan
                         [{"sku": "H1", "qty": 2}, {"sku": "H2", "qty": 2}])

    def test_completed_trial_is_not_replenished_when_held_for_judgment(self):
        pricing = {**self.PRICING, "next_window": {**self.PRICING["next_window"], "hold_after_done": True}}
        with mock.patch.object(self, "PRICING", pricing):
            self.assertEqual(self.run_main(None, {"open": True, "closes_at": time.time() + 120}), [{"sku": "H2", "qty": 2}])

    def test_a_trial_waiting_for_its_window_keeps_the_lead_time_empty(self):
        self.assertEqual(self.run_main([], {"open": False, "opens_at": time.time() + 30}), [])


class PendingAndNextWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        now = time.time()
        self.open_inv = {"balance": 5000000, "units": [], "restock": {"open": True, "closes_at": now + 170}}
        self.cfg = {"id": "t1", "targets": {"F1": 2, "H1": 1, "BIG": 1}}

    def test_sent_lines_wait_until_their_units_show_up(self):
        restock.note_sent(self.md, [{"sku": "H1", "qty": 2}], CAT)
        self.assertEqual(restock.pending(self.md, {"units": []})[0]["qty"], 2)
        arrived = {"units": [dict(unit("x", sku="H1", kind="goods"), supplied_at=time.time())]}
        self.assertEqual(restock.pending(self.md, arrived)[0]["qty"], 1)
        self.assertEqual(restock.pending(self.md, {"units": []}, now=time.time() + restock.PENDING_SECONDS + 1), [])

    def test_buys_the_targets_less_what_is_held_or_waiting_once(self):
        inv = dict(self.open_inv, units=[unit("k", sku="F1")])
        keep = lambda b: restock.guard(b, inv, CAT, CFG, None, [{"sku": "H1", "qty": 1, "worst": 15000}])
        out, why = restock.next_window(self.md, inv, CAT, self.cfg, [{"sku": "H1", "qty": 1, "worst": 15000}], keep)
        self.assertEqual(out, [{"sku": "F1", "qty": 1}, {"sku": "BIG", "qty": 1}])
        came = inv["units"] + [unit("k2", sku="F1", supplied_ago=0), unit("b", sku="BIG", kind="goods", supplied_ago=0)]
        out, why = restock.next_window(self.md, dict(inv, units=came), CAT, self.cfg, [{"sku": "H1", "qty": 1, "worst": 15000}], keep)
        self.assertEqual(out, [])                                # all there: nothing else is bought in this window
        closed = dict(inv, units=came, restock={"open": False, "opens_at": time.time() + 900})
        self.assertIn("bought in the window", restock.next_window(self.md, closed, CAT, self.cfg)[1])
        self.assertIn("done", restock.next_window(self.md, self.open_inv, CAT, self.cfg)[1])

    def test_waits_for_a_window_and_expires(self):
        closed = dict(self.open_inv, restock={"open": False, "opens_at": time.time() + 900})
        self.assertEqual(restock.next_window(self.md, closed, CAT, self.cfg)[0], [])
        later = time.time() + 3 * 3600
        out, why = restock.next_window(self.md, self.open_inv, CAT, self.cfg, now=later)
        self.assertIsNone(out)
        self.assertIn("expired", why)

    def test_a_damaged_record_of_sent_lines_is_not_nothing_sent(self):
        # entries that are not lines are skipped; a file that is not a list raises, and restock.main() then buys nothing
        # (GPT Pro 11/07 04:30 S1: pending not known -> no new stock)
        for junk, raises in (({"a": 1}, True), ([1, {"sku": "H1"}, {"sku": "H1", "qty": "2", "at": time.time()}], False)):
            with open(os.path.join(self.md, restock.SENT), "w") as f:
                json.dump(junk, f)
            if raises:
                self.assertRaises(ValueError, restock.pending, self.md, {"units": []})
            else:
                self.assertEqual(restock.pending(self.md, {"units": []}), [])
            restock.note_sent(self.md, [{"sku": "H1", "qty": 1}], CAT)

    def test_sends_once_per_plan(self):
        out, _ = restock.next_window(self.md, self.open_inv, CAT, self.cfg)
        self.assertTrue(out)
        self.assertEqual(restock.next_window(self.md, self.open_inv, CAT, self.cfg)[0], [])     # units sold meanwhile: not rebought

    def test_never_sends_before_the_window_opens(self):
        soon = dict(self.open_inv, restock={"open": False, "opens_at": time.time() + 20, "closes_at": time.time() + 200})
        self.assertEqual(restock.next_window(self.md, soon, CAT, self.cfg)[0], [])

    def test_a_line_that_never_arrived_is_tried_once_more_alone(self):
        out, _ = restock.next_window(self.md, self.open_inv, CAT, self.cfg)
        self.assertEqual({b["sku"] for b in out}, {"F1", "H1", "BIG"})
        came = [dict(unit("f1", sku="F1"), supplied_at=time.time()), dict(unit("f2", sku="F1"), supplied_at=time.time()),
                dict(unit("h", sku="H1", kind="goods"), supplied_at=time.time())]
        closed = dict(self.open_inv, units=came, restock={"open": False, "opens_at": time.time() + 900})
        self.assertIn("retried", restock.next_window(self.md, closed, CAT, self.cfg)[1])
        out, _ = restock.next_window(self.md, dict(self.open_inv, units=came), CAT, self.cfg)
        self.assertEqual(out, [{"sku": "BIG", "qty": 1}])
        why = restock.next_window(self.md, dict(closed), CAT, self.cfg)[1]
        self.assertIn("never arrived: ['BIG']", why)                # once only
        self.assertIn("done", restock.next_window(self.md, self.open_inv, CAT, self.cfg)[1])

    def test_off_without_a_plan(self):
        self.assertEqual(restock.next_window(self.md, self.open_inv, CAT, {}), (None, "no next_window plan"))


class SurgeTest(unittest.TestCase):
    """Patch 3.2: the caps reserve the surge cost, a SKU high under surge buys less or nothing."""
    CFG = {**CFG, "max_units_per_sku": 20, "max_stock_cost": 10 ** 9, "priority_skus": ["F1"]}

    def inv(self, *rows):
        return {"balance": 50000000, "units": [], "surge": [{"sku": s, "units": 0, "factor": f} for s, f in rows]}

    def test_the_worst_cost_follows_the_factor_and_the_line_size(self):
        f = restock.surge(self.inv(("H1", 1.2), ("F1", 9)))
        self.assertEqual(f, {"H1": 1.2, "F1": 1.5})
        self.assertEqual(restock.worst("H2", 3, CAT, f), 0)                       # not in the catalog
        self.assertAlmostEqual(restock.worst("F2", 3, CAT, f), 40000 * 0.75)       # not under surge
        self.assertAlmostEqual(restock.worst("H1", 3, CAT, f), 20000 * 0.75 * (1.2 + 0.004 + 0.05))
        self.assertAlmostEqual(restock.worst("F1", 3, CAT, f), 40000 * 0.75 * 1.5)
        self.assertEqual(restock.surge({"surge": "x"}), {})
        self.assertEqual(restock.surge({"surge": [{"sku": "H1", "factor": True}, {"factor": 1.3}]}), {})

    def test_half_from_1_20_unless_listed_and_none_from_1_30(self):
        cfg = {**self.CFG, "max_units_per_sku": 4}
        buy = [{"sku": "H1", "qty": 4}, {"sku": "F1", "qty": 2}, {"sku": "F2", "qty": 2}]
        out = restock.guard(buy, self.inv(("H1", 1.22), ("F1", 1.25), ("F2", 1.3)), CAT, cfg)
        self.assertEqual(out, [{"sku": "H1", "qty": 2}, {"sku": "F1", "qty": 2}])      # half of the 4-unit cap, listed whole
        inv = dict(self.inv(("H1", 1.22), ("F1", 1.35)), units=[unit("h", sku="H1", kind="goods")])
        out = restock.guard(buy, inv, CAT, cfg)
        self.assertEqual(out, [{"sku": "H1", "qty": 1}, {"sku": "F1", "qty": 1}, {"sku": "F2", "qty": 2}])   # one held of two; listed keeps one
        inv["units"].append(unit("f", sku="F1"))
        self.assertEqual([b["sku"] for b in restock.guard(buy, inv, CAT, cfg)], ["H1", "F2"])          # F1 has its one unit

    def test_the_stock_cap_counts_the_surge_cost(self):
        cfg = {**self.CFG, "max_stock_cost": 100000}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 8}], self.inv(), CAT, cfg), [{"sku": "H1", "qty": 6}])
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 8}], self.inv(("H1", 1.1)), CAT, cfg), [{"sku": "H1", "qty": 5}])

    def test_sent_lines_reserve_the_surge_cost(self):
        with tempfile.TemporaryDirectory() as md:
            restock.note_sent(md, [{"sku": "H1", "qty": 2}], CAT, factors={"H1": 1.2})
            self.assertAlmostEqual(restock.pending(md, {"units": []})[0]["worst"], 20000 * 0.75 * 1.252)


if __name__ == "__main__":
    unittest.main()
