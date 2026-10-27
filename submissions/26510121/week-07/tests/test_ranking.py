"""Ranking rewards, identity boundaries and reproducible bounded experiments."""
from pathlib import Path
import tempfile
import unittest

from negotiation.experience import Experience
from negotiation.ranking import budget_band, select_buyer, seller_assignment, unit_draw


def shop(name):return {'name':name,'url':'https://'+name+'.example','reviews':[]}
def record(task,key='key-a',budget=130,outcome='purchased',seconds=5,price=110):
    return {'name':'a','item':'lamp','budget':budget,'outcome':outcome,'price':price,
            'task_id':task,'order_id':'o-'+task,'pubkey':key,'seconds':seconds,
            'turns':1,'epoch':100,'sequence':1,'signature_verified':True}


class RankingTests(unittest.TestCase):
    def identity(self):
        return next(str(i) for i in range(100) if unit_draw(str(i),'buyer-exploration')>=0.2)

    def test_saved_per_time_prefers_repeatable_fast_savings(self):
        evidence={'a':{'finalized':100,'successes':50,'mean_success_price':90,'mean_cycle_seconds':100,'last_observed_epoch':100},
                  'b':{'finalized':50,'successes':40,'mean_success_price':100,'mean_cycle_seconds':5,'last_observed_epoch':100}}
        picked,_,detail=select_buyer([shop('a'),shop('b')],evidence,'lamp',140,self.identity(),now=100)
        self.assertEqual(picked['name'],'b')
        self.assertEqual(detail['policy_context']['objective'],'settled saved credits per order-cycle second')

    def test_unpaid_cheap_promises_do_not_outvote_observed_success(self):
        evidence={'a':{'finalized':20,'successes':0,'mean_cycle_seconds':1},
                  'b':{'finalized':10,'successes':8,'mean_success_price':110,'mean_cycle_seconds':10}}
        picked,_,_=select_buyer([shop('a'),shop('b')],evidence,'lamp',140,self.identity(),now=100)
        self.assertEqual(picked['name'],'b')

    def test_failed_capability_and_integrity_are_temporarily_excluded(self):
        for key in ['acp_available','recent_integrity_fault','recent_unavailable']:
            evidence={'a':{key:False if key=='acp_available' else True},'b':{}}
            picked,_,_=select_buyer([shop('a'),shop('b')],evidence,'lamp',140,'order',now=100)
            self.assertEqual(picked['name'],'b')
        with self.assertRaisesRegex(ValueError,'no_currently_healthy_shop'):
            select_buyer([shop('a')],{'a':{'acp_available':False}},'lamp',140,'o')

    def test_experiment_is_stable_and_does_not_exploit_tiny_samples(self):
        stats={'arm_results':[{'arm':a,'matured_opportunities':1,'reward_sum':999} for a in ['memory-v1-control','conservative','balanced','premium']]}
        one=seller_assignment('task',stats)
        self.assertEqual(one,seller_assignment('task',stats))
        self.assertIsNone(one['winner_supported'])
        self.assertEqual(one['version'],'ranking-v2')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.memory=Experience(Path(self.tmp.name)/'db')
    def tearDown(self):self.tmp.cleanup()

    def add(self,row,*,rnd=3,paid=True,total=None):
        self.memory.buyer_observation(row,round_number=rnd)
        self.memory.buyer_feedback(task=row.get('task_id'),order=row['order_id'],verified=paid,
                                   settled=paid and rnd>=3,round_number=rnd,total_seconds=total)

    def test_failure_without_receipt_is_zero_reward_and_contributes_cycle_time(self):
        self.add(record('win'),total=5)
        failed={**record('fail',outcome='no_deal',price=None,seconds=95),'task_id':None}
        self.add(failed,paid=False,total=95)
        evidence=self.memory.buyer_evidence('lamp',130,{'a':'key-a'},round_number=3,now=100)['a']
        self.assertEqual((evidence['finalized'],evidence['successes']),(2,1))
        self.assertEqual(evidence['mean_cycle_seconds'],50)

    def test_new_key_and_different_round_or_budget_band_do_not_inherit_success(self):
        self.add(record('same'))
        self.add(record('other-round'),rnd=2)
        self.add(record('other-budget',budget=200))
        current=self.memory.buyer_evidence('lamp',130,{'a':'key-a'},round_number=3,now=100)['a']
        self.assertEqual(current['successes'],1)
        changed=self.memory.buyer_evidence('lamp',130,{'a':'key-new'},round_number=3,now=100)['a']
        self.assertEqual(changed['successes'],0)
        self.assertEqual(changed['legacy_or_other_key_excluded'],3)

    def test_capability_expires_and_is_bound_to_signing_key(self):
        self.memory.capability('a','key-a',False,at=100)
        self.assertFalse(self.memory.buyer_evidence('lamp',130,{'a':'key-a'},now=101)['a']['acp_available'])
        self.assertNotIn('acp_available',self.memory.buyer_evidence('lamp',130,{'a':'new'},now=101)['a'])
        self.assertNotIn('acp_available',self.memory.buyer_evidence('lamp',130,{'a':'key-a'},now=401)['a'])

    def test_shop_summary_shares_only_confirmed_prices(self):
        self.memory.set_round(3);self.add(record('win'))
        shared=self.memory.seller_policy_summary('lamp',100,now=100)['shared_observed_prices']
        self.assertEqual(shared['median'],110)
        self.assertNotIn('budget',shared)
        self.assertNotIn('pubkey',shared)

    def test_censored_opportunity_is_zero_fixed_horizon_reward_not_rejection(self):
        self.memory.set_round(3)
        self.memory.seller_decision(task='abandoned',item='lamp',reserve=100,turn=1,action='ask',price=115,
                                    buyer_offer=None,round_number=3,policy_arm='balanced',at=100)
        stats=self.memory.seller_policy_summary('lamp',100,now=800)
        row=next(r for r in stats['arm_results'] if r['arm']=='balanced')
        self.assertEqual((row['matured_opportunities'],row['reward_sum'],row['unresolved']),(1,0,1))
        self.assertEqual(stats['opening_price_results'][0]['rejected'],0)


if __name__=='__main__':unittest.main()
