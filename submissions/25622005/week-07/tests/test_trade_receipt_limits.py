"""Accept real discounts while rejecting overcharges and mismatched receipts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trade_memory import evaluate_trade


class ReceiptLimitTests(unittest.TestCase):
    def trade(self, price, **overrides):
        return {'state': 'TASK_STATE_COMPLETED', 'last_offer': 105, 'task_id': 'task-one',
                'receipt': {'item': 'lamp', 'price': price, 'task': 'task-one', **overrides}}

    def test_discount_is_a_deal_and_receipt_is_retained(self):
        trade = self.trade(100)
        before = dict(trade['receipt'])
        self.assertEqual(evaluate_trade(trade, item='lamp', budget=120)[1], 'deal')
        self.assertEqual(trade['receipt'], before)

    def test_above_accepted_offer_is_invalid_even_within_budget(self):
        self.assertEqual(evaluate_trade(self.trade(106), item='lamp', budget=120)[1], 'invalid_receipt')

    def test_budget_limit_remains_authoritative(self):
        self.assertEqual(evaluate_trade(self.trade(100), item='lamp', budget=90)[1], 'invalid_receipt')

    def test_item_and_task_still_must_match(self):
        for fields in ({'item': 'chair'}, {'task': 'different-task'}):
            self.assertEqual(evaluate_trade(self.trade(100, **fields), item='lamp', budget=120)[1],
                             'invalid_receipt')


if __name__ == '__main__':
    unittest.main()
