"""The buyer's value gate (user 11/6 22:5x: not one deal may lower saved)."""
import unittest

from agents import buyer_core as b

RATES = {"A": 0.03, "B": 0.12, "C": 0.32}


class ValueGate(unittest.TestCase):
    def test_food_value_is_the_share_kept(self):
        # patch 3.6: the share of the margin kept - food cost ~50% of list, so 30% off keeps 0.30 / 0.50 of it
        self.assertAlmostEqual(b.deal_value(0.7, "food", None, 0, RATES), 0.6)
        self.assertAlmostEqual(b.deal_value(0.4, "food", None, 0, RATES), 1.0)   # below cost counts the whole margin

    def test_claimed_grade_counts_as_it_is(self):
        self.assertEqual(b.assumed_grade("C", 0), "C")           # 11/7: was reckoned as A and refused at payment
        self.assertEqual(b.assumed_grade(None, 0), "A")
        self.assertLess(b.deal_value(0.6, "goods", b.assumed_grade("C", 0), 0, RATES), 0.67)

    def test_no_stock_replies(self):
        for t in ("Sorry, we have none of that in stock.", "Sorry, none of that in stock right now.", "Out of stock",
                  "재고가 없습니다"):
            self.assertTrue(b.NO_STOCK.search(t), t)
        for t in ("No deal.", "We have plenty in stock at 5000", "재고 있습니다"):
            self.assertFalse(b.NO_STOCK.search(t), t)

    def test_no_warranty_pays_grade_a_only(self):
        self.assertEqual(b.assumed_grade("C", 6), "C")
        self.assertEqual(b.assumed_grade(None, 6), "B")

    def test_full_warranty_turns_a_break_into_zero(self):
        # broken under a 6 h warranty is refunded: 0, not -1
        self.assertAlmostEqual(b.unrefunded("goods", "C", 6, RATES), 0.0)
        self.assertAlmostEqual(b.unrefunded("goods", "C", 3, RATES), 0.16)
        self.assertAlmostEqual(b.unrefunded("goods", "C", 0, RATES), 0.32)

    def test_max_share_meets_the_bar(self):
        for kind, g, w in (("food", None, 0), ("goods", "A", 0), ("goods", "B", 6), ("goods", "C", 2)):
            s = b.max_share(0.27, kind, g, w, RATES)
            self.assertAlmostEqual(b.deal_value(s, kind, g, w, RATES), 0.27)

    def test_grade_c_without_warranty_is_never_worth_it_near_list(self):
        self.assertLess(b.deal_value(0.9, "goods", "C", 0, RATES), 0)

    def test_firm_retry_takes_the_named_lowest_once(self):
        class Fake(b.BuyerCore):
            def __init__(self):
                self.order, self.clock, self.ctx = {"budget": 20000}, lambda: 0, None
            def rules(self): return True
            def deadline(self): return 200
            def bar(self): return 0.69
        f = Fake()
        c = {"list_price": 14900}
        said = lambda p: [{"from": "shop", "price": p, "claimed_grade": None, "warranty_hours": None}]
        self.assertEqual(f.firm_retry_price(c, "food", said(7367)), 7367)        # 0.49 of list: below cost, 1.0
        self.assertIsNone(f.firm_retry_price(c, "food", said(12000)))            # 0.81 of list: under the bar
        self.assertIsNone(f.firm_retry_price(c, "food", said(25000)))            # over the budget and the list
        f._firm = 7367
        self.assertIsNone(f.firm_retry_price(c, "food", said(7000)))             # only one retry


if __name__ == "__main__":
    unittest.main()
