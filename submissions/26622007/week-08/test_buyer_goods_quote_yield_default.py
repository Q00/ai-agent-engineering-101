"""Normal quote-slot policy, separate from the immutable historical finite trial."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import market_buyer as mb
import buyer_goods_quote_yield30 as policy
import test_buyer_goods_quote_yield30 as finite


class DefaultQuoteYield(unittest.TestCase):
    dispatch = finite.QuoteYieldDispatch.dispatch
    prefetch = finite.QuoteYieldDispatch.prefetch
    enable = finite.QuoteYieldDispatch.enable

    def setUp(self):
        finite.QuoteYieldDispatch.setUp(self)
        (Path(self.md)/'buyer_goods_quote_yield.json').unlink()

    def test_normal_default_dispatches_same_valid_slots_without_trial_bytes_or_journal(self):
        policy.assign(self.md,self.order)
        early,quotable=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        self.assertEqual(self.dispatch(early),['d','c'])
        self.assertEqual([s['name'] for s in quotable[:2]],['d','c'])
        self.assertEqual(policy.fields(self.order),{})
        self.assertEqual(list(Path(self.md).glob(policy.PREFIX+'*')),[])

    def test_kill_off_malformed_and_switch_off_after_plan_keep_baseline(self):
        policy.switch_default(self.md,enabled=False)
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual(self.dispatch(early),['a','c'])
        for raw in ('[]','bad','{"enabled": 1}'):
            (Path(self.md)/policy.DEFAULT_SWITCH).write_text(raw)
            self.assertFalse(policy.default_enabled(self.md))
        policy.switch_default(self.md,enabled=True)
        self.order.pop(policy.KEY,None)
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        policy.switch_default(self.md,enabled=False)
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertEqual(policy.fields(self.order),{})

    def test_finite_planned_before_end_never_promotes_to_default_after_end(self):
        self.enable(); early,_=self.prefetch()
        self.clock.return_value=self.cfg['end']
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertFalse(policy.measurement(self.order)['applied'])
        # A different normal order after E can use the default without changing the flag.
        flag=(Path(self.md)/policy.SWITCH).read_bytes()
        self.order={k:v for k,v in self.order.items() if not k.startswith('_stock') and k!=policy.KEY}
        self.order.update(order='normal-after-end',_buyer_started_at=self.cfg['end'])
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual(self.dispatch(early),['d','c'])
        self.assertEqual(policy.fields(self.order),{})
        self.assertEqual((Path(self.md)/policy.SWITCH).read_bytes(),flag)

    def test_normal_rows_are_learned_but_historical_trial_b_stays_excluded(self):
        import buyer_rank_pg as rank
        import buyer_shop_profile as profile
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row
        policy.assign(self.md,self.order)
        early,_=self.prefetch();self.dispatch(early)
        normal=dict(row('fixture','goods',11000,10900,ts=finite.NOW,order='normal'),
            limit=10000,asks=[11000,11000],**policy.fields(self.order))
        old=dict(normal,order='historical',goods_quote_yield30=dict(arm='B',applied=True))
        self.assertFalse(policy.applied_b(normal))
        self.assertEqual(mb.priced([normal,old]),[normal])
        p=Path(self.md)/'measure'/'orders.jsonl'
        p.write_text(''.join(json.dumps(r)+'\n' for r in [normal,old]))
        self.assertEqual(mb.recent_orders(self.md,finite.NOW,3),[normal])
        stats=rank.stats_from_rows([normal,old],{'normal':'confirmed'},finite.NOW)
        self.assertEqual(stats['pool']['goods']['n'],1)
        expanded=[dict(normal,order=f'n-{i}') for i in range(40)]
        excluded=[dict(old,order=f'b-{i}',price=11000) for i in range(80)]
        self.assertEqual(profile.profile_from_rows(expanded+excluded,{},finite.NOW),{'fixture':109})
        (Path(self.md)/'payments.jsonl').write_text(''.join(json.dumps(dict(order=n,paid=b))+'\n'
            for n,b in [('normal',True),('historical',False)]))
        self.assertEqual(b1.paid_rates(self.md,now=finite.NOW)[1],1.0)

    def test_normal_listing_io_drift_uses_original_baseline_and_no_metadata(self):
        from types import SimpleNamespace
        path=Path(self.md)/'shops.json'
        path.write_text(json.dumps([dict(name=s['name'],url=s['url'],reviews=s['reviews'],offers=s['usable'])
                                   for s in self.shops]))
        self.order.update(_stock_shops_path=str(path),_stock_all_shop_names=[s['name'] for s in self.shops])
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        self.rows.append(dict(ts=finite.NOW,kind='goods',category='fixture',quotes=[dict(
            shop='c',sku='G11111111',list_price=10000,ask=1000,fit=True)]))
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertGreater(self.dispatch_io_count,0)
        self.assertTrue(all(any(s is original for original in self.shops) for s in early))
        def record(order):
            return mb.order_record(order,SimpleNamespace(budget=10000,item='goods'),self.shops,{},'a','choice',
                self.shops[0],'chosen',self.shops[0]['usable'][0],6000,.1,dict(state='NO_VALUE',history=[]),
                None,dict(receipt=None),[],[],False,None,{}, {})
        rec=record(self.order)
        self.assertEqual({q['shop'] for q in rec['quotes']},{'a','c'})
        self.assertTrue(rec['quote_attempts_complete'])
        baseline=record({k:v for k,v in self.order.items() if k!=policy.KEY})
        self.assertEqual(json.dumps(rec),json.dumps(baseline))

    def test_normal_overbudget_pool_and_empty_history_keep_existing_safety(self):
        self.shops.append(dict(name='f',url='https://f.example/rpc',reviews=[],usable=[dict(
            sku='G33333333',name='fixture',list_price=12000,qty=1)]))
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual(self.dispatch(early),['d','c'])
        self.assertEqual(policy.fields(self.order),{})
        self.order={k:v for k,v in self.order.items() if k!=policy.KEY and not k.startswith('_stock')}
        self.order['order']='no-history'
        (Path(self.md)/'measure'/'orders.jsonl').write_text('')
        policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual(self.dispatch(early),['a','b'])  # all fresh: baseline review order

    def test_malformed_or_pending_finite_controller_never_becomes_normal(self):
        for raw in ('bad','[]',json.dumps(self.cfg)):
            (Path(self.md)/policy.SWITCH).write_text(raw)
            os.utime(Path(self.md)/policy.SWITCH,(finite.NOW-10,finite.NOW-10))
            with patch.object(policy.time,'time',return_value=finite.NOW-2):
                order=dict(kind='goods',order='before',_buyer_started_at=finite.NOW-2)
                policy.assign(self.md,order)
            self.assertNotIn(policy.KEY,order)

    def test_invalid_default_arrival_clock_or_identity_is_untreated(self):
        for started,now in ((.5,True),(.5,float('inf')),(-1,finite.NOW),(True,finite.NOW),
                            (None,finite.NOW),(float('nan'),finite.NOW)):
            order=dict(kind='goods',order='invalid-clock',_buyer_started_at=started)
            policy.assign(self.md,order,now=now)
            self.assertNotIn(policy.KEY,order)

    def test_resumed_finite_order_cannot_be_reassigned_normal_after_end(self):
        self.enable()
        original={k:v for k,v in self.order.items() if k!=policy.KEY}
        self.clock.return_value=self.cfg['end']
        for started in (finite.NOW,self.cfg['end']):
            resumed=dict(original,_buyer_started_at=started)
            policy.assign(self.md,resumed)
            self.assertNotIn(policy.KEY,resumed)


if __name__=='__main__': unittest.main()
