"""loss_watch.py (the shop's [loss] lines), the scorecard's profit with losses, and the counterpart patterns."""
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import agent_tools
import loss_watch
import market_buyer
import market_model as mm
import scorecard

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1800000000.0


class LossWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.md, self.root = os.path.join(self.tmp, ".market"), self.tmp
        os.makedirs(self.md)
        os.makedirs(os.path.join(self.tmp, "logs"))
        self.env = mock.patch.dict(os.environ, {"AGENT_MEMORY_DIR": os.path.join(self.tmp, "mem")})
        self.env.start()
        self.lines = []

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp)

    def inv(self, units, **kw):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"round": 3, "balance": 4000000, "start": 5000000, "units": units, **kw}, f)

    def run_it(self, now, sold=()):
        return loss_watch.check(self.md, self.root, now=now, sold=list(sold), emit=self.lines.append)

    def test_food_that_drops_a_grade_then_expires(self):
        food = {"unit": "u1", "sku": "F1", "name": "김치", "kind": "food", "grade": "A", "cost": 9000, "expires_at": NOW + 7200}
        self.inv([food])
        self.assertEqual(self.run_it(NOW), [])
        self.inv([dict(food, grade="B")])
        self.assertEqual([x["kind"] for x in self.run_it(NOW + 60)], ["grade_drop"])
        self.inv([])                                         # gone at the market after its date
        out = self.run_it(NOW + 7300)
        self.assertEqual([(x["kind"], x["amount"]) for x in out], [("expired", 9000)])
        self.assertEqual(self.run_it(NOW + 7400), [])        # once
        self.assertTrue(any(l.startswith("[loss] expired 김치 9000") for l in self.lines))

    def test_a_unit_sold_before_its_date_is_no_loss(self):
        self.inv([{"unit": "u1", "sku": "F1", "kind": "food", "grade": "A", "cost": 9000, "expires_at": NOW + 86400}])
        self.run_it(NOW)
        self.inv([])
        self.assertEqual(self.run_it(NOW + 60), [])

    def test_food_sold_just_before_its_date_is_no_loss(self):
        self.inv([{"unit": "u1", "sku": "F1", "kind": "food", "grade": "C", "cost": 9000, "expires_at": NOW + 60}])
        self.run_it(NOW)
        with open(os.path.join(self.tmp, "logs", "market-kit.log"), "w") as f:
            f.write("[acp] settled 김치 at 5000, ledger #3, unit u1 grade C\n[acp] settled half a li")
        self.inv([])
        self.assertEqual(self.run_it(NOW + 120), [])
        with open(os.path.join(self.md, "loss_state.json")) as f:
            self.assertEqual(list(json.load(f)["mine"]), ["3"])          # the unfinished line waits

    def test_storage_once_an_hour(self):
        self.inv([{"unit": "g1", "sku": "H1", "kind": "goods", "grade": "A", "cost": 240000}], storage_per_day=0.01)
        self.run_it(NOW)
        self.assertEqual(self.run_it(NOW + 1800), [])
        out = self.run_it(NOW + 3600)
        self.assertEqual([(x["kind"], x["amount"]) for x in out], [("storage", 100)])     # 240,000 x 1% / 24

    def test_a_unit_i_sold_that_broke_under_my_warranty_is_a_refund(self):
        self.inv([])
        with open(os.path.join(self.tmp, "logs", "market-kit.log"), "w") as f:
            f.write("10:00:00 [acp] settled 청소기 at 30000, ledger #11, unit u9 grade B\n")
        rows = [{"k": "settle", "seq": 11, "ts": NOW, "buyer": "b1", "sku": "H1", "amount": 30000},
                {"k": "settle", "seq": 12, "ts": NOW, "buyer": "b2", "sku": "H1", "amount": 25000},       # another shop's
                {"k": "broken", "seq": 20, "ts": NOW + 3600, "buyer": "b1", "sku": "H1"},
                {"k": "broken", "seq": 21, "ts": NOW + 3600, "buyer": "b2", "sku": "H1"}]
        with open(os.path.join(self.md, mm.ROWS), "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        out = self.run_it(NOW + 4000, sold=[{"unit": "u9", "result": "deal", "warranty": 4}])
        self.assertEqual([(x["kind"], x["amount"]) for x in out], [("break_refund", 30000)])
        self.assertEqual(self.run_it(NOW + 4100), [])
        with open(os.path.join(self.md, "loss_state.json")) as f:
            self.assertEqual(json.load(f)["totals"]["break_refund"], {"n": 1, "amount": 30000})

    def test_no_warranty_and_unknown_warranty(self):
        self.inv([])
        with open(os.path.join(self.tmp, "logs", "market-kit.log"), "w") as f:
            f.write("[acp] settled A at 10000, ledger #1, unit a grade A\n[acp] settled B at 20000, ledger #2\n")
        rows = [{"k": "settle", "seq": 1, "ts": NOW, "buyer": "b", "sku": "H1", "amount": 10000},
                {"k": "settle", "seq": 2, "ts": NOW, "buyer": "c", "sku": "H2", "amount": 20000},
                {"k": "broken", "seq": 3, "ts": NOW + 7200, "buyer": "b", "sku": "H1"},
                {"k": "broken", "seq": 4, "ts": NOW + 7200, "buyer": "c", "sku": "H2"}]
        with open(os.path.join(self.md, mm.ROWS), "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        out = self.run_it(NOW + 8000, sold=[{"unit": "a", "result": "deal", "warranty": 1}])
        self.assertEqual(sorted((x["kind"], x["amount"]) for x in out), [("break_no_warranty", 0), ("break_unknown_warranty", 20000)])

    def test_a_payment_refused_for_the_warranty_is_noted_once(self):
        self.inv([])
        with open(os.path.join(self.md, "payments.jsonl"), "w") as f:
            f.write(json.dumps({"order": "o", "shop": "s", "item": "H1", "price": 9000, "paid": False,
                                "reason": "warranty 0 h, the policy asks for 6"}) + "\n")
        self.assertEqual([x["kind"] for x in self.run_it(NOW)], ["checkout_refused_warranty"])
        self.assertEqual(self.run_it(NOW + 60), [])

    def test_before_round_three_nothing(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"round": 2, "units": []}, f)
        self.assertEqual(self.run_it(NOW), [])

    def test_the_scorecard_shows_losses_and_both_profits(self):
        self.inv([{"unit": "g1", "sku": "H1", "kind": "goods", "grade": "A", "cost": 50000},
                  {"unit": "f1", "sku": "F1", "kind": "food", "grade": "expired", "cost": 9000, "expires_at": 1}])
        self.run_it(time.time())
        card = scorecard.build(self.root)["shop"]
        self.assertEqual(card["profit_if_stock_is_worthless"], -1000000)
        self.assertEqual(card["profit_if_stock_sells_at_cost"], -950000)
        self.assertEqual(card["losses"]["expired"]["amount"], 9000)


class CounterpartTest(unittest.TestCase):
    CATALOG = {"H1": {"list_price": 10000}}

    def test_a_shops_ledger_pattern_once_it_has_enough_deals(self):
        rows = [{"k": "settle", "shop": "https://s.example/", "sku": "H1", "amount": 6000 + 200 * i} for i in range(10)]
        self.assertIsNone(market_buyer.shop_pattern(rows[:9], "https://s.example", self.CATALOG))
        text = market_buyer.shop_pattern(rows, "https://s.example", self.CATALOG)
        self.assertIn("median 70%", text)
        self.assertIsNone(market_buyer.shop_pattern(rows, "https://other.example", self.CATALOG))

    def test_my_customers_tool(self):
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "loss_state.json"), "w") as f:
                json.dump({"mine": {"1": {}, "2": {}}}, f)
            with open(os.path.join(tmp, "catalog.json"), "w") as f:
                json.dump(self.CATALOG, f)
            rows = [{"k": "settle", "seq": 1, "ts": NOW, "buyer": "b", "sku": "H1", "amount": 8000},
                    {"k": "settle", "seq": 2, "ts": NOW, "buyer": "b", "sku": "H1", "amount": 9000},
                    {"k": "settle", "seq": 3, "ts": NOW, "buyer": "c", "sku": "H1", "amount": 9000}]
            with open(os.path.join(tmp, mm.ROWS), "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            out = json.loads(agent_tools.run("my_customers", {}, tmp, folder=os.path.join(HERE, "tools")))
            self.assertEqual(out["customers"], [{"buyer": "b", "deals": 2, "median_share_of_list": 0.9, "lowest_share": 0.8,
                                                 "top_skus": [["H1", 2]], "broke": 0}])
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
