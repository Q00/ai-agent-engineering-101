"""Forecasts stay separate from evidence; critics learn only observed rewards."""
from pathlib import Path
import tempfile
import time
import unittest

from negotiation.experience import Experience
from negotiation.ranking import budget_band
from negotiation.world_model import buyer_forecast, seller_forecast, critique


class WorldModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.memory=Experience(Path(self.tmp.name)/'db')
    def tearDown(self):self.tmp.cleanup()

    def buyer(self,order,price=110,paid=True,arm='saved-rate-selection'):
        row={'name':'s','item':'lamp','budget':130,'outcome':'purchased' if price else 'no_deal',
             'price':price,'task_id':'t-'+order,'order_id':order,'pubkey':'key','seconds':10,
             'turns':1,'epoch':time.time(),'sequence':1,'signature_verified':True}
        self.memory.buyer_prediction(order,{'name':'s','pubkey':'key'},'lamp',budget_band('lamp',130),3,arm,buyer_forecast({},'lamp',130))
        self.memory.buyer_observation(row,round_number=3)
        self.memory.buyer_feedback(task=row['task_id'],order=order,verified=True,settled=paid,round_number=3,total_seconds=10)

    def test_prior_is_not_recorded_as_a_successful_observation(self):
        prediction=buyer_forecast({},'lamp',130)
        self.assertFalse(prediction['is_observation'])
        self.memory.buyer_prediction('o',{'name':'s','pubkey':'key'},'lamp','medium',3,'saved-rate-selection',prediction)
        self.assertNotIn('buyer_observation',self.memory.status())
        self.assertEqual(self.memory.buyer_critic_summary('lamp','medium',3)['arms'],{})

    def test_unpaid_receipt_gets_zero_actual_saved_and_negative_advantage(self):
        self.buyer('unpaid',paid=False)
        value=self.memory.event('buyer_critic','unpaid')
        self.assertEqual(value['reward'],0)
        self.assertLess(value['advantage_rate'],0)
        self.memory.update_buyer_critic('unpaid')
        self.assertEqual(self.memory.status()['buyer_critic'],1)

    def test_critic_adjusts_control_share_only_with_comparable_observations(self):
        for i in range(10):
            self.buyer('c'+str(i),price=110,arm='v1-selection-control')
            self.buyer('n'+str(i),price=129)
        summary=self.memory.buyer_critic_summary('lamp','medium',3)
        self.assertTrue(summary['prefer_control']);self.assertEqual(summary['control_share'],0.5)
        self.memory.set_contract_scope('fixture:other-quality')
        self.assertEqual(self.memory.buyer_critic_summary('lamp','medium',3)['arms'],{})

    def test_shop_keeps_censored_and_late_settlement_revisions_without_double_count(self):
        now=time.time();self.memory.set_round(3)
        self.memory.seller_decision(task='t',item='lamp',reserve=100,turn=1,action='ask',price=115,
                                    buyer_offer=None,round_number=3,policy_arm='balanced',at=now-700)
        prediction=seller_forecast({'market_round':3},'balanced',100,115)
        self.memory.shop_prediction('t','lamp','balanced',prediction)
        first=self.memory.update_shop_critic('t',now=now)
        self.assertEqual(first['reward'],0)
        self.memory.seller_settlement(task='t',item='lamp',price=105,order='o',ledger=7)
        self.assertEqual(self.memory.status()['shop_critic'],2)
        summary=self.memory.seller_policy_summary('lamp',100,now=time.time())
        self.assertEqual(summary['critic']['samples'],1)

    def test_local_failure_does_not_recalibrate_counterparty_success(self):
        value=critique(buyer_forecast({},'lamp',130),0,10,False,attribution_unknown=True)
        self.assertIsNone(value['brier_error'])
        self.assertEqual(value['attribution'],'not established')


if __name__=='__main__':unittest.main()
