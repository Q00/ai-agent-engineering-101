"""Stock rejections schedule a short retry; they do not establish incompatibility."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from buyer_selection import reuse_failures
from buyer_policy import load_policy
from test_buyer_selection import shop


class StockCooldownTests(unittest.TestCase):
    def setUp(self):
        self.shops = [shop('sold'), shop('alternative')]
        self.row = dict(time=datetime.fromtimestamp(1000, timezone.utc).isoformat(), round=3, name='sold', url=self.shops[0]['url'], item='G00001',
            result='no_deal', observed_stock_qty=1, observed_receipt=None,
            negotiation_diagnostics={'reason': 'seller_rejected', 'receipt_seen': False,
                'seller_terminal': {'state': 'TASK_STATE_REJECTED', 'reported_tags': ['stock']}})

    def select(self, rows=None, now=1100, shops=None, budget=90000):
        return reuse_failures(shops or self.shops, [self.row] if rows is None else rows,
            item='G00001', budget=budget, config=load_policy(), order={'round':3, 'kind':'goods'}, now=now)

    def test_budget_reviews_do_not_reset_but_quantity_url_and_timeout_do(self):
        self.shops[0]['reviews'] = [{'stars': 5, 'review':'new'}]
        self.assertEqual([s['name'] for s in self.select(budget=120000)[0]], ['alternative'])
        for field, value in [('qty', 2), ('url', 'https://new.invalid')]:
            changed = deepcopy(self.shops)
            if field == 'qty': changed[0]['offers'][0][field] = value
            else: changed[0][field] = value
            self.assertEqual(len(self.select(shops=changed)[0]), 2)
        self.assertEqual(len(self.select(now=1300)[0]), 2)

    def test_round_receipt_network_and_later_success_do_not_block(self):
        for change in ({'round':2}, {'observed_receipt':{'price':100}},
                       {'negotiation_diagnostics':{'reason':'protocol_or_network_error'}}):
            self.assertEqual(len(self.select(rows=[{**self.row, **change}])[0]), 2)
        success = {**self.row, 'time':datetime.fromtimestamp(1050, timezone.utc).isoformat(), 'result':'settled', 'ledger':1}
        self.assertEqual(len(self.select(rows=[self.row, success])[0]), 2)

    def test_all_cooling_remains_retryable(self):
        selected, info = self.select(shops=self.shops[:1])
        self.assertEqual(len(selected), 1)
        self.assertTrue(info['all_cooling_reconsidered'])
