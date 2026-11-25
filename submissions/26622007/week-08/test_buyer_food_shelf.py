import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import buyer_food_shelf as fs   # noqa: E402
import market_buyer             # noqa: E402

# Real lines, allowed fields only: .market/measure/orders.jsonl and .market/payments.jsonl, yeonsong2/G3434758a,
# 11/09 07:23-07:41 KST (review/W-20261109-0738-AXPro주기)
ORDERS = [
    {"ts": 1794176596.6, "order": "o-8246bb540692", "kind": "food", "shop": "yeonsong2", "sku": "G3434758a",
     "use_within_hours": 540.0, "passed_to_kit": True, "state": "TASK_STATE_COMPLETED"},
    {"ts": 1794177145.0, "order": "o-d881328bf69e", "kind": "food", "shop": "yeonsong2", "sku": "G3434758a",
     "use_within_hours": 540.0, "passed_to_kit": True, "state": "TASK_STATE_COMPLETED"},
    {"ts": 1794178618.6, "order": "o-fa980d307ca5", "kind": "food", "shop": "yeonsong2", "sku": "G3434758a",
     "use_within_hours": 540.0, "passed_to_kit": True, "state": "TASK_STATE_COMPLETED"},
]
PAYS = [
    {"order": "o-8246bb540692", "item": "G3434758a", "price": 13540, "paid": True, "reason": None},
    {"order": "o-d881328bf69e", "item": "G3434758a", "price": 10474, "paid": False,
     "reason": "not paying: 499.12 h of shelf life left, the policy asks for 540.1"},
    {"order": "o-fa980d307ca5", "item": "G3434758a", "price": 13358, "paid": False,
     "reason": "not paying: 238.43 h of shelf life left, the policy asks for 540.1"},
]
NOW = 1794178916.1      # o-1fde72492c5c, the next yeonsong2/G3434758a order (refused, 241.31 h left)


def write(md, orders=ORDERS, pays=PAYS, switch=None, raw_tail=""):
    os.makedirs(os.path.join(md, "measure"), exist_ok=True)
    with open(os.path.join(md, "measure", "orders.jsonl"), "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in orders) + raw_tail)
    with open(os.path.join(md, "payments.jsonl"), "w") as f:
        f.write("".join(json.dumps(r) + "\n" for r in pays) + raw_tail)
    if switch is not None:
        with open(os.path.join(md, fs.SWITCH), "w") as f:
            f.write(switch if isinstance(switch, str) else json.dumps(switch))


ON = {"enabled": True, "study": "food-shelf-test", "salt": "s1"}


def order_in(a, salt="s1"):
    for i in range(200):
        o = f"o-test{i:04d}"
        if fs.arm({"salt": salt}, o) == a:
            return {"order": o, "kind": "food", "use_within_hours": 540.0}
    raise AssertionError


def q(name, sku, surplus, i):
    return (surplus, i, {"name": name, "_offer": {"sku": sku}})


class SwitchTest(unittest.TestCase):
    def test_off_missing_bad_and_env(self):
        with tempfile.TemporaryDirectory() as md:
            write(md)
            self.assertIsNone(fs.config(md))
            for bad in ("{", "[]", {"enabled": "true", "study": "x", "salt": "y"}, {"enabled": True},
                        {"enabled": True, "study": "", "salt": "y"}):
                write(md, switch=bad)
                self.assertIsNone(fs.config(md), bad)
            write(md, switch=ON)
            self.assertEqual(fs.config(md), {"study": "food-shelf-test", "salt": "s1"})
            self.assertIsNone(fs.config(md, env={"BUYER_FOOD_SHELF": "0"}))

    def test_arm_is_fixed_and_split(self):
        arms = [fs.arm({"salt": "s1"}, f"o-{i:06x}") for i in range(2000)]
        self.assertEqual(arms, [fs.arm({"salt": "s1"}, f"o-{i:06x}") for i in range(2000)])
        self.assertTrue(800 < arms.count("B") < 1200)
        self.assertIsNone(fs.arm(None, "o-1"))
        self.assertIsNone(fs.arm({"salt": "s1"}, ""))
        self.assertIsNotNone(fs.arm({"salt": "s1"}, "o-\udcff"))


class HistoryTest(unittest.TestCase):
    def test_last_line_wins_and_ages(self):
        with tempfile.TemporaryDirectory() as md:
            write(md)
            left = fs.last_left(md, now=NOW)
            # the last yeonsong2/G3434758a payment: 238.43 h left at 1794178618.6, 297.5 s before NOW
            self.assertAlmostEqual(left[("yeonsong2", "G3434758a")], 238.43 - 297.5 / 3600, places=3)

    def test_paid_after_refusal_clears(self):
        with tempfile.TemporaryDirectory() as md:
            later = dict(ORDERS[0], ts=1794178700.0, order="o-later")
            write(md, orders=ORDERS + [later], pays=PAYS + [{"order": "o-later", "paid": True, "reason": None}])
            self.assertIsNone(fs.last_left(md, now=NOW)[("yeonsong2", "G3434758a")])

    def test_expired_unit_refusal_counts(self):
        # the kit writes f"{hours:.2f} h" (market_kit.py:526), so an expired unit reads "-3.20 h"
        with tempfile.TemporaryDirectory() as md:
            later = dict(ORDERS[0], ts=1794178700.0, order="o-expired")
            write(md, orders=ORDERS + [later], pays=PAYS + [
                {"order": "o-expired", "paid": False, "reason": "not paying: -3.20 h of shelf life left, the policy asks for 9"}])
            self.assertAlmostEqual(fs.last_left(md, now=NOW)[("yeonsong2", "G3434758a")], -3.20 - 216.1 / 3600, places=3)

    def test_other_refusals_goods_old_and_unpaid_kit_lines_ignored(self):
        with tempfile.TemporaryDirectory() as md:
            orders = [dict(ORDERS[2], order="o-grade", ts=1794178800.0),
                      dict(ORDERS[2], order="o-goods", kind="goods", ts=1794178801.0),
                      dict(ORDERS[2], order="o-old", ts=NOW - 7 * 3600),
                      dict(ORDERS[2], order="o-notkit", passed_to_kit=False, ts=1794178802.0)]
            pays = [{"order": "o-grade", "paid": False, "reason": "not paying: grade C, the policy accepts ['A']"},
                    {"order": "o-goods", "paid": False, "reason": "not paying: 1.00 h of shelf life left, the policy asks for 9"},
                    {"order": "o-old", "paid": False, "reason": "not paying: 1.00 h of shelf life left, the policy asks for 9"},
                    {"order": "o-notkit", "paid": False, "reason": "not paying: 1.00 h of shelf life left, the policy asks for 9"}]
            write(md, orders=orders, pays=pays)
            self.assertEqual(fs.last_left(md, now=NOW), {})

    def test_broken_lines_and_cut_tail(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, raw_tail='{"order": "o-cut", "paid": fal\nnot json\n')
            self.assertIn(("yeonsong2", "G3434758a"), fs.last_left(md, now=NOW))
            old = fs.TAIL_BYTES
            try:
                fs.TAIL_BYTES = 200        # a tail read starts mid-line: the cut first line is dropped, not misread
                self.assertIsInstance(fs.last_left(md, now=NOW), dict)
            finally:
                fs.TAIL_BYTES = old

    def test_odd_line_keeps_the_arm_with_an_error(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, orders=ORDERS + [dict(ORDERS[0], order=["o-list"])], switch=ON)
            for a in "AB":
                st = fs.state(md, order_in(a), now=NOW)
                self.assertEqual((st["arm"], st["left"], st["error"]), (a, {}, "TypeError"))

    def test_missing_files(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            os.remove(os.path.join(md, "payments.jsonl"))
            st = fs.state(md, order_in("B"), now=NOW)
            self.assertEqual(st["left"], {})


class PickTest(unittest.TestCase):
    NEED = 540.1

    def scored(self):
        return [q("yeonsong2", "G3434758a", 7000, 0), q("olstjr", "G3434758a", 4500, 1), q("11im", "G3434758a", 5100, 2)]

    def test_b_drops_short_when_another_remains(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            st = fs.state(md, order_in("B"), now=NOW)
            kept = fs.keep(st, self.scored(), self.NEED)
            self.assertEqual([x[2]["name"] for x in kept], ["olstjr", "11im"])
            self.assertEqual(fs.measurement(st, "11im"),
                             {"study": "food-shelf-test", "arm": "B", "reached": True, "n_scored": 3,
                              "would_drop": ["yeonsong2"], "dropped": ["yeonsong2"], "applied": True, "picked": "11im"})

    def test_b_keeps_all_when_no_other_quote_fits(self):
        # an ask over my limit may end with no talk: no better than the unpaid try (independent review I1)
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            st = fs.state(md, order_in("B"), now=NOW)
            fits = lambda s: s["name"] == "yeonsong2"
            self.assertEqual(fs.keep(st, self.scored(), self.NEED, fits), self.scored())
            m = fs.measurement(st)
            self.assertEqual((m["reached"], m["would_drop"], m["applied"]), (True, [], False))

    def test_b_keeps_all_when_every_quote_short(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            st = fs.state(md, order_in("B"), now=NOW)
            only = [self.scored()[0]]
            self.assertEqual(fs.keep(st, only, self.NEED), only)
            self.assertFalse(fs.measurement(st)["applied"])

    def test_b_keeps_when_need_is_met(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            st = fs.state(md, dict(order_in("B"), use_within_hours=48.0), now=NOW)
            self.assertEqual(fs.keep(st, self.scored(), 48.1), self.scored())

    def test_a_and_off_pick_as_before(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            st = fs.state(md, order_in("A"), now=NOW)
            scored = self.scored()
            self.assertIs(fs.keep(st, scored, self.NEED), scored)       # arm A: the same list, the same pick
            m = fs.measurement(st)
            self.assertEqual((m["arm"], m["would_drop"], m["dropped"], m["applied"]), ("A", ["yeonsong2"], [], False))
            write(md)
            os.remove(os.path.join(md, fs.SWITCH))
            self.assertIsNone(fs.state(md, order_in("B"), now=NOW))
            self.assertEqual(fs.keep(None, self.scored(), self.NEED), self.scored())

    def test_goods_and_no_use_within_out_of_study(self):
        with tempfile.TemporaryDirectory() as md:
            write(md, switch=ON)
            self.assertIsNone(fs.state(md, dict(order_in("B"), kind="goods")))
            self.assertIsNone(fs.state(md, {"order": "o-x", "kind": "food"}))
            self.assertIsNone(fs.measurement(None))


class BestQuoteFoodShelf(unittest.TestCase):
    """market_buyer.best_quote with the study's state on the order: a (cheaper) is yeonsong2-like, short of the need."""
    def run_quotes(self, st, asks=(6000, 6500)):
        def shop(n):
            return {"name": n, "url": f"https://{n}.example", "reviews": [],
                    "usable": [{"sku": "G3434758a", "name": "삼다수", "list_price": 10000}]}

        def fake_quote(s, label, key, o, out, ctl=None):
            out[s["name"]] = {"ask": dict(zip("ab", asks))[s["name"]], "text": "", "url": s["url"],
                              "task": {"id": "t-" + s["name"], "status": {"state": "TASK_STATE_INPUT_REQUIRED"}},
                              "refused": None}
        order = {"order": "o-x", "kind": "food", "use_within_hours": 540.0}
        if st is not None:
            order["_food_shelf"] = st
        s = [shop("a"), shop("b")]
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()):
            best, got = market_buyer.best_quote(s[0], s[1:], order, {}, 20000, lambda x: None, {"quote_shops": 2},
                                                wait=1, rounds=1)
            time.sleep(0.05)
        return best["name"], order

    def test_arm_b_picks_the_shop_that_can_be_paid(self):
        st = {"study": "t", "arm": "B", "left": {("a", "G3434758a"): 238.4}}
        name, order = self.run_quotes(st)
        self.assertEqual(name, "b")
        self.assertEqual(fs.measurement(order["_food_shelf"])["dropped"], ["a"])

    def test_arm_a_off_and_need_met_pick_the_cheaper(self):
        self.assertEqual(self.run_quotes({"study": "t", "arm": "A", "left": {}})[0], "a")
        self.assertEqual(self.run_quotes(None)[0], "a")
        self.assertEqual(self.run_quotes({"study": "t", "arm": "B", "left": {("a", "G3434758a"): 600.0}})[0], "a")
        self.assertEqual(self.run_quotes({"study": "t", "arm": "B", "left": {("a", "G3434758a"): None}})[0], "a")

    def test_arm_b_keeps_the_short_shop_when_the_other_ask_is_over_my_limit(self):
        st = {"study": "t", "arm": "B", "left": {("a", "G3434758a"): 238.4}}
        name, order = self.run_quotes(st, asks=(6000, 9900))
        self.assertEqual(name, "a")
        self.assertFalse(fs.measurement(order["_food_shelf"])["applied"])

    def test_a_bad_state_never_stops_the_order(self):
        name, order = self.run_quotes({"study": "t", "arm": "B", "left": None})
        self.assertEqual(name, "a")


if __name__ == "__main__":
    unittest.main()
