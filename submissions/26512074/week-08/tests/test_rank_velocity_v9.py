import json
import math
import unittest
from unittest.mock import patch
from tests.test_integrated_strategy import Case
from buyer_time_policy import action_values
from strategy_policy import database


class DecisionTests(unittest.TestCase):
    def test_skip_cooldown_preserves_a_good_current_trade(self):
        self.assertLess(action_values(.1,.1,.5,2,.1)['settle'],0)
        result=action_values(.1,.1,.5,2,.1,1,10)
        self.assertGreater(result['settle'],0)
        self.assertFalse(result['continue_bargaining'])

    def test_unknown_wait_does_not_fabricate_advantage(self):
        self.assertEqual(action_values(.1,.2,.5,2,.1),action_values(.1,.2,.5,2,.1,1,None))

    def test_saved_crossing_uses_cumulative_denominator(self):
        from score_coordination import saved_trades
        s,n,target,q=.5888,14731,.59,.9
        count=saved_trades(s,n,target,q)
        self.assertGreaterEqual((s*(n+100)+q*count)/(n+100+count),target)
        self.assertLess((s*(n+100)+q*(count-1))/(n+99+count),target)
        self.assertIsNone(saved_trades(s,n,target,.59))


class RuntimeTests(Case):
    def test_pre_v9_collector_fallback_is_cached_without_network(self):
        from round3_metrics import cached_waits
        events=[dict(seq=i,kind=k,ts=self.now+t,data=dict(order=o,buyer='totato2203-r3')) for i,(k,t,o) in enumerate([
            ('order',-100,'a'),('settle',-95,'a'),('result',-90,'a'),('order',-88,'b'),('result',-80,'b'),('order',-60,'c')],1)]
        with database(self.md,True) as c:
            c.executemany('insert into market_events values(?,?,?,?)',[(e['seq'],e['ts'],e['kind'],json.dumps(e)) for e in events])
        groups=cached_waits(self.md,self.now)
        self.assertEqual(groups['settled']['n'],1)
        self.assertAlmostEqual(groups['unsettled']['mean'],20)
        with patch('sqlite3.connect',side_effect=AssertionError('must reuse cache')):
            self.assertEqual(cached_waits(self.md,self.now+10),groups)

    def test_stale_champion_requires_supported_replacement(self):
        from price_learning import quote_episode
        self.policy['price_exploration_fraction']=0
        unit=dict(unit='u',sku='x',grade='A')
        self.write('price-hierarchy.json',dict(diagnostics={'x:A':dict(stale=True,fallback_arm=1.)}))
        arm,*_=quote_episode(self.md,'t',unit,self.now,2.,[1.,2.],self.policy,True,{})
        self.assertEqual(arm,2.)
        self.write('price-hierarchy.json',dict(diagnostics={'x:A':dict(stale=True)},challengers={'x:A':dict(arm=1.2)}))
        arm,*_=quote_episode(self.md,'t2',unit,self.now+180,2.,[1.,2.],self.policy,False,{})
        self.assertEqual(arm,1.2)

    def test_measured_waits_separate_settlement_and_no_settlement(self):
        from round3_metrics import update
        def event(seq,kind,ts,order,**data):
            return dict(seq=seq,kind=kind,ts=ts,data=dict(buyer='totato2203-r3',order=order,**data))
        events=[event(1,'order',self.now-100,'a'),event(2,'settle',self.now-95,'a'),
                event(3,'result',self.now-90,'a'),event(4,'order',self.now-88,'b'),
                event(5,'result',self.now-80,'b'),event(6,'order',self.now-60,'c')]
        update(self.md,events,self.catalog,self.now)
        waits=json.loads((self.md/'buyer-opportunity.json').read_text())['next_wait_by_outcome']
        self.assertEqual(waits['settled']['mean'],2)
        self.assertAlmostEqual(waits['unsettled']['mean'],20)

    def test_v9_review_preserves_v8_state(self):
        import batch_strategy_review as review
        old=self.md/'strategy-v8-review.json';old.write_text('{"status":"complete"}')
        new=self.md/'strategy-v9-review.json';new.write_text(json.dumps(dict(status='scheduled',controller='existing_market_worker',pid=1,start=1000,cohort_end=22600,review_at=44200)))
        with patch.object(review,'MD',self.md):
            self.assertEqual(review.tick(2000)['status'],'scheduled')
        self.assertEqual(old.read_text(),'{"status":"complete"}')
