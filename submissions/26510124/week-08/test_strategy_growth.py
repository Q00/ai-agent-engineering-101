"""Synthetic policy regressions; no live trades, network or model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from adaptive_stock import candidates, depth_cap
from buyer_policy import buying_terms, load_policy, negotiation_policy
from market_buyer import Buyer, PolicyRefusal
from replenishment_profit import refund_recovery
from sales_performance import sku_metrics
from shop_policy import policy_at, selling_terms, shop_copy
from warranty_trial import WarrantyTrial

ROOT = Path(__file__).resolve().parent


class StrategyGrowthTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / '.market')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.policy = policy_at()
        self.product = {'sku': 'G12345678', 'grade': 'B', 'kind': 'goods', 'cost': 50000}
        self.base = selling_terms(self.product, self.policy)

    def history(self, count=10, **overrides):
        sold = self.now - 7 * 3600
        return {'schema': 1, 'round': 3, 'updated_at': self.now, 'sales': [
            {'ledger': i+1, 'unit': f'unit-{i}', 'sku': self.product['sku'], 'category': 'beds',
             'grade': 'B', 'kind': 'goods', 'price': 100000, 'cost': 50000,
             'supplied_at': sold-3600, 'settled_at': sold, 'observed_at': sold,
             'receipt_time': sold-1, 'warranty_hours': 0, **overrides} for i in range(count)]}

    def terms(self, memory, product=None):
        (self.path/'sales-performance.json').write_text(json.dumps(memory))
        return WarrantyTrial(self.path).terms(product or self.product, self.base, self.policy, 3, self.now)

    def test_profit_after_refund_can_replenish_but_not_unknown_or_loss(self):
        memory = self.history()
        memory['sales'][0]['refunds'] = [{'amount': 100000}]
        metric = sku_metrics(memory, self.now)[self.product['sku']]
        cfg = self.policy['restock']
        self.assertTrue(refund_recovery(metric, cfg))
        self.assertEqual(depth_cap(metric, cfg), 2)
        for changes in ({'unknown': 1}, {'mature': 9}, {'net_estimate': 1}, {'refunded': 2},
                        {'mature_revenue': 0}, {'pending': 100, 'mature': 1}):
            self.assertFalse(refund_recovery({**metric, **changes}, cfg))

    def test_b_trial_uses_same_grade_mature_history_and_reserved_floor(self):
        terms = self.terms(self.history(5))
        self.assertEqual(terms['warranty_hours'], 6)
        self.assertGreaterEqual(terms['closing_price_floor'] * .85, self.product['cost'])
        self.assertEqual(self.base['warranty_hours'], 0)
        for kind, grade in [('goods', 'A'), ('goods', 'C'), ('food', 'B')]:
            self.assertEqual(self.terms(self.history(), {**self.product,'kind':kind,'grade':grade}), self.base)
        self.assertEqual(self.terms(self.history(5, grade='A')), self.base)

    def test_uninsured_history_must_still_wait_six_hours(self):
        for age in (0, 5*3600+3599):
            self.assertEqual(self.terms(self.history(5, settled_at=self.now-age)), self.base)
        self.assertEqual(self.terms(self.history(4)), self.base)

    def test_loss_refunds_ambiguity_and_stale_memory_disable_trial(self):
        for memory in [self.history(price=55000), self.history(5, refunds=[{'amount':100000}]),
                       self.history(5, ambiguous_refund=True), self.history(5, cost=None),
                       {**self.history(), 'round':2}, {**self.history(), 'updated_at':self.now-1801}]:
            self.assertEqual(self.terms(memory), self.base)
        memory=self.history();memory['sales'].append(deepcopy(memory['sales'][0]))
        self.assertEqual(self.terms(memory), self.base)

    def test_reader_is_cached_and_does_not_keep_stale_or_bad_memory(self):
        (self.path/'sales-performance.json').write_text(json.dumps(self.history()))
        reader=WarrantyTrial(self.path)
        self.assertIsNotNone(reader.read(self.now,3))
        with patch.object(Path,'open',side_effect=AssertionError('Repeated read')):
            self.assertIsNotNone(reader.read(self.now+30,3))
            self.assertIsNone(reader.read(self.now+30,2))
        (self.path/'sales-performance.json').write_text('{')
        self.assertIsNone(reader.read(self.now+61,3))

    def test_candidate_recovery_keeps_demand_surge_and_exclusion_gates(self):
        import test_turnover_candidates as fixture
        f=fixture.TurnoverCandidateTests();f.setUp();self.addCleanup(f.doCleanups)
        sku=fixture.CHEAP[0]
        f.metrics[sku]={'mature':10,'refunded':1,'net_estimate':100000,'mature_revenue':1000000,
                        'speed_score':1,'unknown':0}
        reasons={}
        self.assertIn(sku, f.pool(diagnostics=reasons))
        f.inv['surge']=[{'sku':sku,'factor':1.2,'units':150}]
        self.assertNotIn(sku,f.pool(diagnostics=reasons))
        self.assertEqual(reasons[sku],'surge_above_limit')
        f.inv['surge']=[];f.metrics[sku]['refunded']=2
        self.assertNotIn(sku,f.pool(diagnostics=reasons))
        self.assertEqual(reasons[sku],'unknown_cost_or_unprofitable_refund_history')

    def test_buyer_completion_zone_does_not_raise_or_replace_hard_authority(self):
        for kind, expected in [('food',70000),('goods',72000)]:
            terms=buying_terms(100000,product={'list_price':100000},
                              order={'round':3,'kind':kind,'use_within_hours':6})
            self.assertEqual(negotiation_policy(terms)['completion_price'],expected)
            self.assertEqual(terms['price_limit'],85000)
            buyer=Buyer(Mock(),url='https://shop.invalid',item='sku',budget=100000,policy=terms)
            buyer.validate_decision({'action':'accept','price':75000,'reason':'specific comparable advantage'},'price: 75000')
            with self.assertRaises(PolicyRefusal):
                buyer.validate_decision({'action':'offer','price':85001,'reason':'bad'},'price: 85001')
        terms=buying_terms(50000,product={'list_price':100000},order={'round':3,'kind':'goods'})
        self.assertEqual(terms['completion_price'],45000)

    def test_buyer_invalid_completion_policy_rejected_and_legacy_supported(self):
        cfg=load_policy()
        for value in (None,{}, {'food':59,'goods':72},{'food':70,'goods':86},{'food':True,'goods':72}):
            with patch('buyer_policy.read_json',return_value={**cfg,'completion_list_price_percent':value}):
                with self.assertRaises(ValueError):load_policy()
        cfg.pop('completion_list_price_percent')
        terms=buying_terms(100000,product={'list_price':100000},order={'round':3,'kind':'goods'},config=cfg)
        self.assertNotIn('completion_price',terms)

    def test_card_does_not_claim_all_b_goods_are_covered(self):
        self.assertIn('Selected B-grade',shop_copy(self.policy))
        self.assertIn('check the quoted',shop_copy(self.policy))


if __name__ == '__main__':
    unittest.main()
