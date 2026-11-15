"""buyer_margin_cap: Patch 3.6 Saved is the share of the shop's margin kept, so the limit and the pick read the grade."""
import json
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import buyer_margin_cap as mc
import market_buyer


def switch(d=None):
    md = tempfile.mkdtemp()
    if d is not None:
        with open(os.path.join(md, mc.SWITCH), "w", encoding="utf-8") as f:
            json.dump(d, f)
    return md


class Config(unittest.TestCase):
    def test_missing_or_broken_switch_is_off(self):
        self.assertIsNone(mc.config(switch()))
        self.assertIsNone(mc.config(switch({"enabled": False})))
        self.assertIsNone(mc.config(switch({"enabled": "yes"})))
        self.assertIsNone(mc.config(switch([1])))

    def test_on_with_the_default_or_a_sane_target(self):
        self.assertEqual(mc.config(switch({"enabled": True}))["target"], 0.75)
        self.assertEqual(mc.config(switch({"enabled": True, "target": 0.80}))["target"], 0.80)
        for bad in (True, "0.8", 0.2, 1.5, float("nan")):
            self.assertIsNone(mc.config(switch({"enabled": True, "target": bad})), bad)

    def test_take_is_on_only_when_the_switch_says_true(self):
        self.assertFalse(mc.config(switch({"enabled": True}))["take"])
        for v in ("true", 1, None, False):
            self.assertFalse(mc.config(switch({"enabled": True, "take": v}))["take"], v)
        self.assertTrue(mc.config(switch({"enabled": True, "take": True}))["take"])

    def test_env_zero_is_off(self):
        self.assertIsNone(mc.config(switch({"enabled": True}), env={"BUYER_MARGIN_CAP": "0"}))


class Score(unittest.TestCase):
    """Worked from the rule: (list - price) / (list - cost), 1 at or under cost, the cost even over its grade's range
    (A 55-75%, B 40-60%, C 25-45% of list)."""

    def test_expected_share_of_margin(self):
        ln = math.log
        self.assertAlmostEqual(mc.expected(0.60, "B", 0), 0.40 * ln(0.60 / 0.40) / 0.20, places=5)   # at the top cost
        self.assertAlmostEqual(mc.expected(0.60, "C", 0), 0.40 * ln(0.75 / 0.55) / 0.20, places=5)   # over every cost
        self.assertAlmostEqual(mc.expected(0.60, "A", 0), (0.15 + 0.40 * ln(0.45 / 0.40)) / 0.20, places=5)
        self.assertAlmostEqual(mc.expected(0.50, "B", 0.1),                                         # a refunded break: 0
                               0.9 * (0.10 + 0.50 * ln(0.60 / 0.50)) / 0.20, places=5)
        self.assertAlmostEqual(mc.expected(0.50, "A", 0), 1.0, places=5)   # under every cost: all of it, never more
        self.assertAlmostEqual(mc.expected(1.0, "B", 0), 0.0)

    def test_a_cheaper_ask_of_the_same_grade_always_ranks_first(self):
        # re-review W-20261114-1038 I1: the middle cost made every ask under it 1.0, so 0.58 and 0.64 of a grade A
        # list tied and the pick fell to the rival penalty and the quote order
        for g in ("A", "B", "C", None):
            for b in (0, 0.1):
                ps = [i / 100 for i in range(0, 101)]
                vals = [mc.expected(p, g, b) for p in ps]
                self.assertTrue(all(x > y for x, y in zip(vals, vals[1:])), (g, b))
        self.assertGreater(mc.expected(0.58, "A", 0), mc.expected(0.64, "A", 0))
        self.assertGreater(mc.expected(0.30, "A", 0), mc.expected(0.40, "A", 0))   # both under every A cost
        self.assertGreater(mc.expected(0.45, "B", 0), mc.expected(0.50, "B", 0))
        self.assertLessEqual(mc.expected(0.0, "A", 0), 1.0 + 1e-5)

    def test_an_unnamed_grade_is_scored_as_b(self):
        self.assertAlmostEqual(mc.expected(0.60, None, 0), mc.expected(0.60, "B", 0))

    def test_cap_is_the_price_that_keeps_the_target(self):
        cfg = {"target": 0.80}
        self.assertEqual(mc.cap(cfg, 10000, "B", 0), 6000)
        self.assertEqual(mc.cap(cfg, 10000, "C", 0), 4800)
        self.assertEqual(mc.cap(cfg, 10000, "A", 0), 7200)
        self.assertEqual(mc.cap(cfg, 10000, "B", 0.2), 5000)

    def test_a_grade_that_cannot_reach_the_target_has_no_price(self):
        self.assertEqual(mc.cap({"target": 0.80}, 10000, "C", 0.30), 0)   # 70% survive: 0.70 at most, even at cost

    def test_off_or_no_grade_is_no_cap(self):
        self.assertIsNone(mc.cap(None, 10000, "B", 0))
        self.assertIsNone(mc.cap({"target": 0.80}, 10000, None, 0))
        self.assertIsNone(mc.cap({"target": 0.80}, 0, "B", 0))

    def test_goods_take_the_grades_chance_food_none(self):
        stats = {"sold": 1000, "rate": 0.1, "by_sku": {}, "by_grade": {"C": {"sold": 500, "broke": 150}}, "by_sku_grade": {}}
        self.assertAlmostEqual(mc.chance("goods", stats, "G1", "C", 0.05), 0.30)
        self.assertAlmostEqual(mc.chance("goods", None, "G1", "C", None), mc.BREAK["C"])
        self.assertAlmostEqual(mc.chance("goods", None, "G1", "A", 0.2), 0.2)     # never under the SKU's
        self.assertEqual(mc.chance("food", stats, "G1", "C", 0.05), 0.0)


class Lowered(unittest.TestCase):
    def test_only_a_talk_the_cap_lowered(self):
        self.assertTrue(mc.lowered({"margin_cap": {"cap": 6000, "limit_before": 6800}}))
        self.assertFalse(mc.lowered({"margin_cap": {"cap": 7200, "limit_before": 6800}}))
        self.assertFalse(mc.lowered({"margin_cap": None}))
        self.assertFalse(mc.lowered({}))

    def test_the_shop_profile_leaves_a_lowered_talk_out(self):
        import buyer_shop_profile
        row = {"ts": 1000.0, "asks": [6500, 6500], "limit": 6000, "shop": "s", "state": "TASK_STATE_CANCELED", "kind": "food"}
        with_cap = dict(row, margin_cap={"cap": 6000, "limit_before": 6799})
        self.assertEqual(buyer_shop_profile.profile_from_rows([with_cap] * 30, {}, 1001.0),
                         buyer_shop_profile.profile_from_rows([], {}, 1001.0))
        self.assertNotEqual(buyer_shop_profile.profile_from_rows([row] * 30, {}, 1001.0),
                            buyer_shop_profile.profile_from_rows([], {}, 1001.0))


def shop(name):
    return {"name": name, "url": f"https://{name}.example", "reviews": [],
            "usable": [{"sku": "G1", "name": "item", "list_price": 10000}]}


class BestQuote(unittest.TestCase):
    def pick(self, asks, margin, kind="food"):
        def fake_quote(s, label, key, o, out, ctl=None):
            ask, text = asks[s["name"]]
            out[s["name"]] = {"ask": ask, "text": text, "url": s["url"], "refused": None,
                                "task": {"id": f"t-{s['name']}", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}
        s = [shop(n) for n in asks]
        with mock.patch.object(market_buyer, "quote", fake_quote), mock.patch.object(market_buyer, "log"), \
                mock.patch.object(market_buyer, "buyer_rpc", lambda url, m, p: None), \
                mock.patch.object(market_buyer, "cancel_quietly", lambda *a, **k: None), \
                mock.patch.object(market_buyer, "STARTED", time.time()), \
                mock.patch.object(market_buyer, "MARGIN_CAP", margin):
            best, _ = market_buyer.best_quote(s[0], s[1:], {"kind": kind}, {}, 20000, lambda x: None,
                                              {"quote_shops": len(s)}, wait=1.0, rounds=1)
        return best["name"]

    ASKS = {"c": (5500, "G1, grade C: asking 5500"), "a": (6400, "G1, grade A: asking 6400")}

    def test_off_the_cheaper_ask_wins_as_before(self):
        self.assertEqual(self.pick(self.ASKS, None), "c")

    def test_on_the_grade_a_ask_near_its_cost_wins_over_a_cheaper_grade_c(self):
        # C at 55% of list keeps 45/65 = .69 of the margin and is over its cap (48%); A at 64% keeps all of it
        self.assertEqual(self.pick(self.ASKS, {"cfg": {"target": 0.80}, "stats": None}), "a")

    def test_on_two_asks_within_the_cap_rank_by_the_margin_kept(self):
        asks = {"c": (4700, "G1, grade C: asking 4700"), "b": (5200, "G1, grade B: asking 5200")}
        # C keeps 53/65 = .815, B 48/50 = .96
        self.assertEqual(self.pick(asks, {"cfg": {"target": 0.80}, "stats": None}), "b")
        self.assertEqual(self.pick(asks, None), "c")

    def test_on_nothing_fits_a_grade_no_price_can_buy_does_not_take_the_pick(self):
        # goods at target .75: grade C has no price (cap 0) though 4500 keeps more of its margin than A at 8000;
        # A's limit (6799, its cap 7293) is within the talk band of 8000, so the talk is held with a (review I1)
        asks = {"c": (4500, "G1, grade C: asking 4500"), "a": (8000, "G1, grade A: asking 8000")}
        self.assertEqual(self.pick(asks, {"cfg": {"target": 0.75}, "stats": None}, kind="goods"), "a")
        self.assertEqual(self.pick(asks, None, kind="goods"), "c")


class Limit(unittest.TestCase):
    def test_margin_limit_reads_the_quotes_grade(self):
        o = {"sku": "G1", "list_price": 10000}
        with mock.patch.object(market_buyer, "MARGIN_CAP", {"cfg": {"target": 0.80}, "stats": None}):
            self.assertEqual(market_buyer.margin_limit(o, "food", {"text": "grade B, asking 7000"}, None)["cap"], 6000)
            self.assertEqual(market_buyer.margin_limit(o, "goods", {"text": "Grade C, 6 h warranty"}, None)["cap"], 0)
            self.assertIsNone(market_buyer.margin_limit(o, "food", {"text": "asking 7000"}, None))
        self.assertIsNone(market_buyer.margin_limit(o, "food", {"text": "grade B"}, None))


class TakeTop(unittest.TestCase):
    ON, OFF = {"target": 0.75, "take": True}, {"target": 0.75, "take": False}

    def test_the_cap_within_the_budget_and_deal_cap(self):
        self.assertEqual(mc.take_top(self.ON, 7294, 6799, 16000), 7294)
        self.assertEqual(mc.take_top(self.ON, 7294, 6799, 7000), 7000)     # value_limit (budget) under the cap

    def test_none_when_off_or_nothing_over_the_limit(self):
        self.assertIsNone(mc.take_top(self.OFF, 7294, 6799, 16000))
        self.assertIsNone(mc.take_top(None, 7294, 6799, 16000))
        self.assertIsNone(mc.take_top(self.ON, 6000, 6799, 16000))   # the cap lowered the limit: no band over it
        self.assertIsNone(mc.take_top(self.ON, 6799, 6799, 16000))
        self.assertIsNone(mc.take_top(self.ON, 7294, 6799, 6799))    # the budget is the limit
        self.assertIsNone(mc.take_top(self.ON, None, 6799, 16000))
        self.assertIsNone(mc.take_top(self.ON, float("nan"), 6799, 16000))


class Run:
    """market_buyer.main end to end: two shops quote a unit of a 10,000 list at 6,500."""
    TEXT = "컵 (G1), grade B, 6 h warranty: asking 6500"
    CATALOG = {"G1": {"kind": "goods", "category": "가전>컵", "list_price": 10000}}

    def run_main(self, sw, kind="goods", receipt=None):
        printed, calls = [], []
        order = {"order": "o-1", "item": "컵", "budget": 16000, "acceptable": ["G1"], "kind": kind, "category": "가전>컵"}
        if kind == "food":
            order["use_within_hours"] = 24.0

        def fake_quote(s, label, key, o, out, ctl=None):
            out[s["name"]] = {"ask": 6500, "text": self.TEXT, "url": s["url"],
                              "refused": None, "task": {"id": "t", "status": {"state": "TASK_STATE_INPUT_REQUIRED"}}}

        def neg(*a, **k):
            calls.append((a, k))
            if receipt is not None:
                return {"state": "TASK_STATE_COMPLETED", "task": "t", "agreed": receipt["price"], "holds": 0,
                        "history": [("shop asked", 6500), ("you offered", receipt["price"])], "receipt": receipt}
            return {"state": "TASK_STATE_CANCELED", "task": "t", "history": [("shop asked", 6500)], "agreed": None,
                    "holds": 0, "receipt": None}
        with tempfile.TemporaryDirectory() as d:
            sp, op = os.path.join(d, "shops.json"), os.path.join(d, "order.json")
            with open(sp, "w") as f:
                json.dump([{"name": n, "url": f"https://{n}.example", "reviews": [],
                            "offers": [{"sku": "G1", "name": "G1", "qty": 1}]} for n in ("s", "t")], f)
            with open(op, "w") as f:
                json.dump(order, f)
            with open(os.path.join(d, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            if sw is not None:
                with open(os.path.join(d, mc.SWITCH), "w") as f:
                    json.dump(sw, f)
            with mock.patch.object(market_buyer, "MEMORY", os.path.join(d, "m.json")), \
                    mock.patch.object(market_buyer, "SETTLED", os.path.join(d, "settled.jsonl")), \
                    mock.patch.object(market_buyer, "market_round", lambda: 3), \
                    mock.patch.object(market_buyer, "serves_acp", lambda u: True), \
                    mock.patch.object(market_buyer, "buyer_cfg", lambda: {"quote_shops": 2}), \
                    mock.patch.object(market_buyer, "choose", lambda s, m, i, b: (s[0], "t")), \
                    mock.patch.object(market_buyer, "quote", fake_quote), \
                    mock.patch.object(market_buyer, "cancel_quietly", lambda *a, **k: None), \
                    mock.patch.object(market_buyer, "cancel_quote", lambda *a, **k: None), \
                    mock.patch.object(market_buyer, "negotiate", neg), \
                    mock.patch.object(market_buyer, "MARGIN_CAP", None), \
                    mock.patch.object(market_buyer, "AGENT_DIR", market_buyer.AGENT_DIR), \
                    mock.patch.object(market_buyer, "STARTED", time.time()), \
                    mock.patch.dict(os.environ, {"BUYER_MARGIN_CAP": "", "BUYER_SHORT_WARRANTY": "0"}), \
                    mock.patch("builtins.print", lambda *a, **k: printed.append(a[0])), \
                    mock.patch.object(sys, "argv", ["x", "--shops", sp, "--item", "컵", "--budget", "16000", "--order", op]):
                market_buyer.main()
            with open(os.path.join(d, "measure", "orders.jsonl"), encoding="utf-8") as f:
                rows = [json.loads(x) for x in f]
        return rows[-1], calls


class Main(Run, unittest.TestCase):
    """Two shops quote a grade B unit of a 10,000 list at 6,500."""

    def test_off_the_limit_is_the_saved_cap_and_nothing_is_recorded(self):
        for sw in (None, {"enabled": False}):
            row, calls = self.run_main(sw)
            self.assertEqual(row["limit"], 6799)     # saved_cap: int(10000 (1 - 0.32))
            self.assertIsNone(row["margin_cap"])
            self.assertEqual(len(calls), 1)

    def test_on_a_goods_grade_b_quote_is_held_to_its_cap(self):
        row, calls = self.run_main({"enabled": True, "target": 0.80})
        # grade B goods break 11.5% of the time (no ledger): 10000 (1 - 0.80 x 0.50 / 0.885) = 5480
        self.assertEqual(row["limit"], 5480)
        self.assertEqual(row["margin_cap"], {"grade": "B", "chance": 0.115, "cap": 5480, "target": 0.80, "limit_before": 6799})
        self.assertTrue(mc.lowered(row))
        # the quotes are recorded against the limit the pick used (review I3)
        self.assertEqual({q["limit"] for q in row["quotes"]}, {5480})
        self.assertFalse(any(q["fit"] for q in row["quotes"]))

    def test_on_a_goods_grade_c_quote_ends_without_a_talk(self):
        with mock.patch.object(self, "TEXT", "컵 (G1), grade C, 6 h warranty: asking 6500"):
            row, calls = self.run_main({"enabled": True})
        self.assertEqual(row["margin_cap"]["cap"], 0)      # .75 / (1 - .30) > 1: no price keeps the target
        self.assertEqual(row["limit"], 0)
        self.assertEqual(calls, [])
        self.assertTrue(mc.lowered(row))

    def test_on_a_food_grade_b_quote_is_held_to_its_cap(self):
        row, calls = self.run_main({"enabled": True, "target": 0.80}, kind="food")
        self.assertEqual(row["limit"], 6000)
        self.assertEqual(row["margin_cap"]["cap"], 6000)
        self.assertEqual(len(calls), 1)     # 6500 is within the talk band of 6000: the talk goes on, to the cap


class MainTake(Run, unittest.TestCase):
    """take on (W-20261115-1330): a grade A unit of a 10,000 list, cap 10000 (1 - .75 x .35 / .97) = 7293 over the
    saved_cap limit 6799. My offers stay at 6799; the shop's final ask is taken up to 7293 (the talk's ceiling) and
    a receipt at it is paid."""
    TEXT = "컵 (G1), grade A, 6 h warranty: asking 6500"
    REC = {"item": "G1", "price": 7200, "sig_seller": "s", "task": "t", "shop": "s", "order": "o-1", "warranty_hours": 6}

    def test_take_off_is_as_before(self):
        row, calls = self.run_main({"enabled": True})
        self.assertEqual(row["limit"], 6799)
        self.assertNotIn("take_top", row["margin_cap"])
        self.assertIsNone(calls[0][1].get("ceiling"))
        self.assertFalse(mc.lowered(row))

    def test_take_on_the_ceiling_is_the_cap_and_my_limit_holds(self):
        row, calls = self.run_main({"enabled": True, "take": True})
        self.assertEqual(row["limit"], 6799)                 # my own offers: as before
        self.assertEqual(row["margin_cap"]["cap"], 7293)
        self.assertEqual(row["margin_cap"]["take_top"], 7293)
        self.assertEqual(calls[0][1].get("ceiling"), 7293)
        self.assertEqual(calls[0][0][2], 6799)               # negotiate(url, label, limit, ...)
        self.assertFalse(mc.lowered(row))

    def test_take_on_a_receipt_within_the_cap_is_not_over_my_limit(self):
        off, _ = self.run_main({"enabled": True}, receipt=dict(self.REC))
        on, _ = self.run_main({"enabled": True, "take": True}, receipt=dict(self.REC))
        self.assertTrue(any("over my limit" in w for w in off["warnings"]), off["warnings"])
        self.assertFalse(any("over my limit" in w for w in on["warnings"]), on["warnings"])
        over, _ = self.run_main({"enabled": True, "take": True}, receipt=dict(self.REC, price=7294))
        self.assertTrue(any("over my limit 7293" in w for w in over["warnings"]), over["warnings"])

    def test_take_on_a_grade_b_cap_under_the_limit_gets_no_band(self):
        with mock.patch.object(self, "TEXT", "컵 (G1), grade B, 6 h warranty: asking 6500"):
            row, calls = self.run_main({"enabled": True, "target": 0.80, "take": True})
        self.assertEqual(row["limit"], 5480)
        self.assertNotIn("take_top", row["margin_cap"])
        self.assertIsNone(calls[0][1].get("ceiling"))


if __name__ == "__main__":
    unittest.main()
