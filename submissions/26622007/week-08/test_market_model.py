"""market_model and restock's use of it. The ledger is a fake with week-08's sample fields; no network, no harness.

    python -m unittest test_market_model -v        (from submissions/26622007/week-08)
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)
import market_model as mm    # noqa: E402
import restock               # noqa: E402

NOW = 1800000000
CATALOG = {"F1": {"kind": "food", "category": "식품>김치", "list_price": 20000, "shelf_hours": 720},
           "F2": {"kind": "food", "category": "식품>김치", "list_price": 30000, "shelf_hours": 720},
           "F3": {"kind": "food", "category": "식품>우유", "list_price": 10000, "shelf_hours": 24},
           "H1": {"kind": "goods", "category": "가전>컵", "list_price": 40000},
           "H2": {"kind": "goods", "category": "가구>소파", "list_price": 900000}}
DEMAND = [{"category": c, "median_price": 20000} for c in ("식품>김치", "식품>우유", "가전>컵", "가구>소파")]
CFG = {**mm.DEFAULTS, **restock.DEFAULTS, "cover_hours": 24, "min_expected": 0.25, "round_ends_at": None}


def settle(seq, sku="F1", buyer="b1", amount=16000, shop="https://s1", age_hours=0):
    return {"k": "settle", "seq": seq, "ts": NOW - age_hours * 3600, "buyer": buyer, "order": f"o{seq}", "sku": sku,
            "amount": amount, "grade": "A", "honest": True, "shop": shop}


def many(n, start=0, **kw):
    return [settle(start + i, **kw) for i in range(n)]


class FakeLedger:
    """Serves GET /api/ledger?after=N: 500 at most, oldest first; negative N counts back from the newest."""

    def __init__(self, entries):
        self.entries, self.calls = entries, 0

    def __call__(self, after):
        self.calls += 1
        if after < 0:
            return self.entries[after:][:mm.PAGE]
        return [e for e in self.entries if e["seq"] > after][:mm.PAGE]


def entry(seq, kind="settle", rnd=3, **data):
    return {"seq": seq, "ts": NOW, "kind": kind, "round": rnd, "data": data}


class SyncTest(unittest.TestCase):
    def ledger(self, r2=1000, r3=1200):
        rows = [entry(i, "result", 2, buyer="x", order=f"r{i}", shop="https://old") for i in range(1, r2 + 1)]
        for i in range(r2 + 1, r2 + r3 + 1):
            rows.append(entry(i, order=f"o{i}", buyer="b", item="F1", amount=16000, grade="A", claim_honest=True))
        return FakeLedger(rows)

    def test_not_open_yet_means_nothing_and_no_cursor(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mm.sync(d, FakeLedger([entry(1, rnd=2), entry(2, rnd=2)])), 0)
            self.assertFalse(os.path.exists(os.path.join(d, mm.CURSOR)))

    def test_finds_the_start_quickly_and_reads_only_round_3(self):
        led = self.ledger()
        self.assertEqual(mm.first_seq_of_round(led), 1000)
        self.assertLess(led.calls, 15)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mm.sync(d, led), 1200)
            rows = mm.load_rows(d)
            self.assertEqual((rows[0]["seq"], rows[-1]["seq"], rows[0]["sku"]), (1001, 2200, "F1"))

    def test_round_3_starting_at_the_first_entry(self):
        self.assertEqual(mm.first_seq_of_round(FakeLedger([entry(i) for i in range(1, 5)])), 0)

    def test_resumes_where_it_stopped_within_the_page_limit(self):
        led = self.ledger()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mm.sync(d, led, max_pages=1), 500)
            self.assertEqual(mm.sync(d, led, max_pages=1), 500)
            self.assertEqual(mm.sync(d, led), 200)
            self.assertEqual(mm.sync(d, led), 0)
            self.assertEqual(len(mm.load_rows(d)), 1200)

    def test_an_empty_folder_rebuilds_all_of_it(self):
        led = self.ledger()
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            mm.sync(a, led)
            mm.sync(b, led)
            self.assertEqual(mm.load_rows(a), mm.load_rows(b))

    def test_a_lost_cursor_does_not_count_deals_twice(self):
        led = self.ledger()
        with tempfile.TemporaryDirectory() as d:
            mm.sync(d, led)
            os.remove(os.path.join(d, mm.CURSOR))
            mm.sync(d, led)
            self.assertEqual(len(mm.load_rows(d)), 1200)

    def test_keeps_broken_and_result_and_lists_unknown_fields(self):
        led = FakeLedger([entry(1, "order", budget_commitment="x", buyer="b"),
                          entry(2, order="o", buyer="b", item="F1", amount=1, surprise=1),
                          entry(3, "result", order="o", buyer="b", shop="https://s"),
                          entry(4, "broken", buyer="b", item="H1"),
                          entry(5, "strange", a=1)])
        with tempfile.TemporaryDirectory() as d:
            mm.sync(d, led)
            self.assertEqual([r["k"] for r in mm.load_rows(d)], ["settle", "result", "broken"])
            with open(os.path.join(d, mm.CURSOR)) as f:
                fields = json.load(f)["fields"]
            self.assertIn("surprise", fields["settle"])
            self.assertEqual(fields["strange"], ["a"])


class EstimateTest(unittest.TestCase):
    def est(self, rows, **kw):      # one test buyer buying from one test shop is not collusion unless a test says so
        return mm.estimate(rows, CATALOG, kw.pop("me", {"nocha12"}), kw.pop("shops", {"https://me"}), NOW,
                           {"min_settles": 1, "captive_min_deals": 10 ** 6, **kw})

    def test_rate_is_deals_per_hour_and_recent_deals_weigh_more(self):
        fresh = self.est(many(48, age_hours=0) + [settle(99, age_hours=48)])
        old = self.est(many(48, age_hours=48) + [settle(99, age_hours=0)])
        self.assertGreater(fresh["skus"]["F1"]["rate"], old["skus"]["F1"]["rate"] * 2)

    def test_a_lone_sale_leans_on_the_category(self):
        rows = many(100, sku="F1") + [settle(500, sku="F2")]
        m = self.est(rows)["skus"]
        self.assertGreater(m["F1"]["rate"], m["F2"]["rate"])
        self.assertGreater(m["F2"]["rate"], 1.0)           # more than its one deal in an hour: shrunk toward the category

    def test_my_own_buyer_is_not_demand_for_my_shop(self):
        m = self.est(many(50, buyer="nocha12", shop="https://s1"))
        self.assertEqual(m["n"], 0)
        self.assertEqual(m["skus"], {})
        self.assertEqual(m["buyers"]["nocha12"], ["식품>김치"])    # still known as a fact about the buyer

    def test_a_captive_buyer_is_left_out_but_mine_or_split_are_not(self):
        rows = many(30, buyer="loyal", shop="https://rival") + many(10, 100, buyer="free", shop="https://rival") \
            + many(10, 200, buyer="free", shop="https://s2") + many(30, 300, buyer="mine", shop="https://me")
        m = self.est(rows, captive_min_deals=20)
        self.assertEqual(m["captive"], {"loyal": "https://rival"})
        self.assertEqual(m["n"], 50)

    def test_few_deals_do_not_make_a_buyer_captive(self):
        self.assertEqual(self.est(many(5, buyer="new", shop="https://rival"), captive_min_deals=20)["captive"], {})

    def test_shop_comes_from_the_result_when_the_settle_has_none(self):
        rows = many(30, buyer="loyal", shop=None)
        rows += [{"k": "result", "seq": 1000 + r["seq"], "order": r["order"], "shop": "https://rival"} for r in rows]
        self.assertEqual(self.est(rows, captive_min_deals=20)["captive"], {"loyal": "https://rival"})

    def test_share_grows_with_my_sales_and_the_price_is_the_median_paid(self):
        rows = many(10, shop="https://me", amount=14000) + many(10, 50, shop="https://s2", amount=18000)
        m = self.est(rows)["skus"]["F1"]
        self.assertAlmostEqual(m["my_share"], 0.5, places=2)
        self.assertAlmostEqual(m["price_ratio"], 0.8)
        cold = self.est(many(10, shop="https://s2") + many(10, 50, shop="https://s3"))["skus"]["F1"]
        self.assertAlmostEqual(cold["my_share"], 1 / 3, places=2)      # never stocked it: an even split, not zero

    def test_favourites_and_active_threshold(self):
        rows = many(5, buyer="b", sku="F1") + many(2, 50, buyer="b", sku="H1")
        self.assertEqual(self.est(rows)["buyers"]["b"], ["식품>김치", "가전>컵"])
        self.assertFalse(mm.estimate(rows, CATALOG, now=NOW, cfg={"min_settles": 200})["active"])

    def test_unknown_skus_are_ignored(self):
        self.assertEqual(self.est([settle(1, sku="G-gone")])["n"], 0)


def model(**skus):
    base = {"rate": 1.0, "my_share": 0.5, "price_ratio": 0.8, "deals": 30, "buyers": 3, "sellers": 1}
    return {"active": True, "n": 500, "skus": {k: {**base, **v} for k, v in skus.items()},
            "cats": {"식품>김치": {"rate": 1}, "가전>컵": {"rate": 1}}, "buyers": {}, "captive": {}}


class PlanWithModelTest(unittest.TestCase):
    def plan(self, m, balance=5000000, units=(), **cfg):
        return restock.plan({"balance": balance, "units": list(units)}, CATALOG, DEMAND, {**CFG, **cfg}, m)["buy"]

    def test_stocks_what_sells_for_the_cover_time(self):
        got = self.plan(model(F1={"rate": 0.5}), explore_lines=0)
        self.assertEqual(got, [{"sku": "F1", "qty": 6}])       # 0.5/h x my half x 24 h

    def test_counts_what_i_have(self):
        units = [{"unit": str(i), "sku": "F1", "category": "식품>김치", "grade": "A"} for i in range(4)]
        self.assertEqual(self.plan(model(F1={"rate": 0.5}), units=units, explore_lines=0), [{"sku": "F1", "qty": 2}])

    def test_best_profit_first_within_the_line_limit(self):
        got = self.plan(model(F1={"rate": 2.0}, F2={"rate": 2.0}, H1={"rate": 0.5}), max_lines=2, explore_lines=0)
        self.assertEqual([b["sku"] for b in got], ["F2", "F1"])

    def test_no_margin_no_stock(self):
        self.assertEqual(self.plan(model(F1={"price_ratio": 0.45}), explore_lines=0), [])

    def test_too_little_expected_is_not_bought(self):
        self.assertEqual(self.plan(model(F1={"rate": 0.01}), explore_lines=0), [])

    def test_food_is_stocked_only_for_what_can_sell_while_fresh(self):
        got = self.plan(model(F3={"rate": 2.0}), explore_lines=0)
        self.assertEqual(got, [{"sku": "F3", "qty": 11}])      # 1/h x 24 h x 0.45

    def test_one_sku_cannot_take_the_shop(self):
        got = self.plan(model(H2={"rate": 5.0}), balance=5000000, explore_lines=0, max_units_per_sku=20)
        self.assertEqual(got, [])                              # one unit is over a tenth of the balance
        self.assertEqual(self.plan(model(H1={"rate": 5.0}), balance=2000000, explore_lines=0),
                         [{"sku": "H1", "qty": 6}])

    def test_never_past_the_cash_line(self):
        self.assertEqual(self.plan(model(F1={"rate": 2.0}), balance=1500000, explore_lines=0), [])

    def test_explores_only_selling_categories_where_i_have_nothing(self):
        got = self.plan(model(F1={"rate": 0.5}), explore_lines=2)
        self.assertEqual(sorted(b["sku"] for b in got), ["F1", "H1"])
        units = [{"unit": "x", "sku": "H1", "category": "가전>컵", "grade": "A"}]
        self.assertEqual([b["sku"] for b in self.plan(model(F1={"rate": 0.5}), units=units, explore_lines=2)], ["F1"])

    def test_warming_up_or_no_model_uses_the_first_stock_rule(self):
        for m in (None, {**model(F3={"rate": 2.0}), "active": False}):
            self.assertEqual(sorted(b["sku"] for b in self.plan(m)), ["F1", "H1"])

    def test_near_the_end_it_buys_only_what_can_sell_in_time(self):
        import time
        soon = lambda h: time.time() + h * 3600
        m = model(F1={"rate": 0.5})
        self.assertEqual(self.plan(m, round_ends_at=soon(4), explore_lines=2), [{"sku": "F1", "qty": 1}])   # 0.25/h x 2 h
        self.assertEqual(self.plan(m, round_ends_at=soon(1), explore_lines=2), [])
        self.assertEqual(self.plan(None, round_ends_at=soon(24)), [])                 # no untested stock at the end
        self.assertTrue(self.plan(None, round_ends_at=soon(100)))
        with open("pricing.json", encoding="utf-8") as f:
            ends = json.load(f)["restock"]["round_ends_at"]
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M", time.gmtime(ends + 9 * 3600)), "2026-11-17 23:59")

    def test_a_ledger_failure_still_plans(self):
        def boom(*a, **k):
            raise OSError("market down")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mm, "sync", boom), \
                mock.patch("builtins.print"):
            self.assertIsNone(restock.read_model(d, CATALOG, CFG))


if __name__ == "__main__":
    unittest.main()
