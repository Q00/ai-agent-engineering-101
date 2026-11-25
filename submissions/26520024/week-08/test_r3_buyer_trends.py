"""Synthetic/offline policy tests; these are not market performance results."""
import copy
from pathlib import Path
import tempfile
import unittest

from r3_buyer_trends import collect, enrich, initial_request, offer, score, select
from r3_memory import Memory
from r3_strategy import savings_ceiling


class BuyerTrendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Memory(Path(self.tmp.name))
        self.addCleanup(self.memory.close)
        self.now = 100000
        self.catalog = {'F': dict(kind='food', category='food>milk', list_price=100),
                        'G': dict(kind='goods', category='furniture', list_price=100)}
        self.order = dict(kind='food', category='food>milk', budget=80, use_within_hours=12)
        self.candidate = dict(shop_name='S', sku='F', kind='food', category='food>milk',
                              list_price=100, target_saved=.2,
                              value_estimate=dict(estimated_price=80, expected_value=100,
                                                  expected_surplus=20, break_risk_estimate=0))

    def record(self, kind, key, data, age=600):
        self.memory.record(kind, key, data, ts=self.now-age)

    def public(self, seq, buyer='other', age=600, **extra):
        data = dict(seq=seq, source='public_ledger', sku='F', amount=50, buyer=buyer)
        data.update(extra)
        self.record('market_settle', seq, data, age)

    def purchase(self, ledger, price=50, hours=24, sku='F'):
        self.record('settlement', ledger, dict(shop='S', sku=sku,
            observed_at=self.now-600, receipt=dict(price=price),
            settled=dict(ledger=ledger, sku=sku, value=100,
                         expires_at=self.now-600+hours*3600)))

    def attempt(self, order, *, paid=True, outcome='receipt_pending', age=600, sku='F', url='https://s.test'):
        self.record('negotiation', order, dict(order=order, shop='S', shop_url=url,
                    sku=sku, outcome=outcome), age)
        if paid is not None:
            self.record('payment', order, dict(order=order, shop=url, paid=paid, ledger=order), age)

    def estimate(self):
        return enrich(copy.deepcopy(self.candidate), collect(self.memory,self.catalog,self.now), self.order)

    def test_public_price_requires_multiple_buyers_and_three_deals(self):
        for i in range(3): self.public(i)
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'], 0)
        self.public(3, buyer='second')
        value=self.estimate()['value_estimate']
        self.assertEqual(value['trend_basis'], 'public_sku_6h_mixed_quality')
        self.assertEqual(value['estimated_price'],65)

    def test_old_backfill_future_and_untrusted_rows_are_excluded(self):
        for i in range(3):
            self.public(i,buyer=str(i),age=30000,observed_at=self.now)
            self.public(i+10,buyer=str(i),age=-1)
            self.public(i+20,buyer=str(i),source='review')
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)

    def test_public_records_never_import_a_different_sku(self):
        for i in range(4):self.public(i,buyer=str(i),sku='G')
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)

    def test_public_duplicate_ledger_ids_are_not_multiple_samples(self):
        for i in range(4):
            self.record('market_settle','copy-'+str(i),dict(seq=1,sku='F',amount=5,
                                                         buyer=str(i),source='public_ledger'))
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)

    def test_nan_boolean_or_missing_price_is_not_evidence(self):
        for i,value in enumerate((True,None,-1,999)):
            self.public(i,buyer=str(i),amount=value)
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)

    def test_food_price_evidence_must_cover_current_usage_horizon(self):
        self.purchase(1,hours=1);self.purchase(2,hours=2)
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)
        self.purchase(3,hours=24);self.purchase(4,hours=25)
        value=self.estimate()['value_estimate']
        self.assertEqual(value['trend_basis'],'own_shop_sku_6h')
        self.assertEqual(value['trend_samples'],2)
        self.assertAlmostEqual(value['estimated_price'],70)

    def test_settlement_requires_positive_usable_value(self):
        for i in range(3):
            self.record('settlement',i,dict(shop='S',sku='F',receipt=dict(price=1),
                                         settled=dict(ledger=i,value=0,expires_at=self.now+90000)))
        self.assertEqual(self.estimate()['value_estimate']['trend_samples'],0)

    def test_success_requires_matching_payment_not_receipt(self):
        self.attempt(1)
        self.attempt(2,paid=None)
        self.attempt(3,paid=False)
        self.attempt(4,paid=None,outcome='no_deal')
        value=self.estimate()['value_estimate']
        self.assertEqual(value['completed_attempts'],3)
        self.assertEqual(value['settled_attempts'],1)
        self.assertAlmostEqual(value['settlement_probability'],3/7,places=4)

    def test_inflight_and_model_errors_do_not_count_as_failure(self):
        self.attempt(1,paid=None,outcome='error')
        self.attempt(2,paid=None,outcome='no_deal',age=10)
        value=self.estimate()['value_estimate']
        self.assertEqual(value['completed_attempts'],0)
        self.assertEqual(value['settlement_probability'],.5)

    def test_history_is_scoped_to_shop_category_kind(self):
        self.attempt(1,sku='G')
        self.assertEqual(self.estimate()['value_estimate']['completed_attempts'],0)

    def test_mismatched_payment_shop_is_ignored(self):
        self.record('negotiation',1,dict(order=1,shop='S',shop_url='https://s.test',sku='F',
                                         outcome='receipt_pending'))
        self.record('payment',1,dict(order=1,shop='https://other.test',paid=True,ledger=5))
        self.assertEqual(self.estimate()['value_estimate']['completed_attempts'],0)

    def test_completion_rate_can_outweigh_extreme_unreliable_discount(self):
        reliable=copy.deepcopy(self.candidate)
        reliable['shop_name']='reliable'
        reliable['value_estimate'].update(estimated_price=65,settlement_probability=.9)
        unreliable=copy.deepcopy(self.candidate)
        unreliable['shop_name']='unreliable'
        unreliable['value_estimate'].update(estimated_price=30,settlement_probability=.05)
        self.assertEqual(select([unreliable,reliable],80)['shop_name'],'reliable')

    def test_smaller_absolute_price_is_not_automatically_better(self):
        large=copy.deepcopy(self.candidate);small=copy.deepcopy(self.candidate)
        large['list_price']=1000;large['value_estimate']['estimated_price']=600
        small['value_estimate']['estimated_price']=80
        self.assertGreater(score(large,1000),score(small,1000))

    def test_opening_is_trend_based_and_cannot_exceed_risk_ceiling(self):
        state=dict(budget=100,list_price=100,target_saved=.2,
                   value_estimate=dict(estimated_price=65,trend_samples=4,break_risk_estimate=0),
                   current_ask=99,last_offer=None,rounds=0)
        self.assertEqual(offer(state)['price'],58)
        for budget in (2,50,100):
            for risk in (0,.5,.99):
                state.update(budget=budget);state['value_estimate']['break_risk_estimate']=risk
                result=offer(state)
                if result['action']=='offer':
                    self.assertLessEqual(result['price'],savings_ceiling(state))
                    self.assertLessEqual(result['price'],budget)

    def test_low_explicit_ask_is_not_raised_to_opening_anchor(self):
        state=dict(budget=100,list_price=100,target_saved=.2,current_ask=40,last_offer=None,
                   value_estimate=dict(estimated_price=65,trend_samples=4,break_risk_estimate=0))
        self.assertEqual(offer(state)['price'],40)

    def test_initial_food_request_discloses_horizon_not_private_budget(self):
        text=initial_request('F',dict(self.order,budget=7654321),
                             dict(min_hours_left=12+5/60))
        self.assertIn('use within 12 hours',text)
        self.assertIn('12.083334 hours',text)
        self.assertNotIn('7654321',text)
        self.assertEqual(initial_request('G',dict(self.order,kind='goods'),{}),
                         'I want to buy one unit of SKU G')


if __name__=='__main__':
    unittest.main()
