"""Finite quote selection at the public prefetch/dispatch boundary; no transport."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_buyer as mb
import buyer_goods_quote_yield30 as trial

NOW = 2000000001


class QuoteYieldDispatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        # Historical finite tests explicitly disable the separate normal policy.
        (Path(self.md)/'buyer_goods_quote_yield.json').write_text('{"enabled": false}')
        self.cfg = dict(enabled=True, study='yield-test', salt='yield-test', start=NOW-1, end=NOW+1799)
        self.shops = [dict(name=n, url='https://'+n+'.example/rpc', reviews=[0]*r,
            usable=[dict(sku='G11111111', name='fixture', list_price=10000, qty=1)])
            for n,r in [('a',10),('b',9),('c',8),('d',1),('e',0)]]
        self.rows = [dict(ts=NOW-1, kind='goods', category='fixture', quotes=[dict(
            shop=n, sku='G11111111', list_price=10000, ask=p, fit=True, lost=None)])
            for n,p in [('a',6500),('b',6500),('d',1000)]]
        (Path(self.md)/'measure').mkdir()
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        self.order = dict(kind='goods', category='fixture', order=next(str(i) for i in range(100)
            if trial.arm(self.cfg,str(i))=='B'), _buyer_started_at=NOW)
        self.clock = patch.object(trial.time,'time',return_value=NOW).start()
        self.addCleanup(patch.stopall)

    def enable(self):
        p=Path(self.md)/trial.SWITCH
        p.write_text(json.dumps(self.cfg)); os.utime(p,(NOW-10,NOW-10))
        trial.assign(self.md,self.order)

    def prefetch(self, k=3, rival=frozenset()):
        return mb.prefetch_pool(self.shops,{'trades':[]},self.md,'fixture',k,rival,
            kind='goods',order=self.order,budget=10000,chance_of=lambda sku:.1)

    def dispatch(self, early, fail=None, order=None, after_start=None):
        sent=[]
        real_thread=mb.threading.Thread
        io_threads=[]
        class QuoteThread:
            def __init__(self, target, args, **kw): self.args=args
            def start(self):
                if self.args[0]['name']==fail: raise RuntimeError('fixture start failure')
                sent.append(self.args[0]['name'])
                if after_start is not None: after_start(self.args[0])
        def thread(target, args=(), **kw):
            if target is mb.quote:
                return QuoteThread(target,args,**kw)
            th=real_thread(target=target,args=args,**kw)
            io_threads.append(th)
            return th
        with patch.object(mb.threading,'Thread',thread):
            try: self.dispatch_threads=mb.start_quotes(early,self.order if order is None else order,10000,lambda sku:.1,{},mb.quote_ctl())
            except RuntimeError:
                if fail is None: raise
        for th in io_threads:
            th.join(1)
            self.assertFalse(th.is_alive())
        self.dispatch_io_count=len(io_threads)
        return sent

    def test_normal_over_budget_selected_lp_does_not_disable_other_valid_b_slots(self):
        over=dict(name='f',url='https://f.example/rpc',reviews=[],usable=[dict(
            sku='G33333333',name='fixture',list_price=12000,qty=1)])
        self.shops.append(over)
        self.assertEqual(mb.selected_offer(over,10000)['list_price'],12000)
        self.enable(); early,_=self.prefetch()
        self.assertNotIn('trace',trial.measurement(self.order))
        self.assertTrue(any(Path(self.md).glob(trial.PREFIX+'*.plan.json')))
        self.assertEqual([s['name'] for s in early],['d','c'])
        self.assertEqual(self.dispatch(early),['d','c'])
        self.assertTrue(trial.measurement(self.order)['applied'])

    def test_off_and_a_exact_baseline_and_b_changes_actual_initial_membership_after_stock_reset(self):
        early,allshops=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertIsNone(trial.measurement(self.order))
        self.enable()
        early,allshops=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])
        self.assertEqual(self.dispatch(early),['d','c'])
        self.assertEqual([s['name'] for s in allshops[:2]],['d','c'])
        self.assertTrue(trial.measurement(self.order)['applied'])

    def test_expiry_after_prefetch_restores_both_list_references_without_treatment(self):
        self.enable(); early,allshops=self.prefetch()
        self.clock.return_value=self.cfg['end']
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertEqual([s['name'] for s in allshops[:2]],['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_thread_start_failure_does_not_mark_but_partial_new_dispatch_does(self):
        self.enable(); early,_=self.prefetch()
        self.assertEqual(self.dispatch(early,fail='d'),[])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_partial_dispatch_marks_once_and_same_initial_batch_cannot_dispatch_twice(self):
        self.enable(); early,_=self.prefetch()
        self.assertEqual(self.dispatch(early,fail='c'),['d'])
        self.assertTrue(trial.measurement(self.order)['applied'])
        self.assertEqual(self.dispatch(early),[])

    def test_copied_initial_caller_fails_closed_and_restores_both_published_references(self):
        self.enable(); early,allshops=self.prefetch()
        self.assertEqual(self.dispatch(list(early)),['a','c'])
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertEqual([s['name'] for s in allshops[:2]],['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])
        self.assertEqual(self.dispatch(early),[])

    def test_current_baseline_membership_change_falls_back_to_original_a_baseline(self):
        self.enable(); early,allshops=self.prefetch()
        # Review I2: drift invalidates B; A's stock reset still uses the original pool.
        self.rows.append(dict(ts=NOW,kind='goods',category='fixture',quotes=[dict(
            shop='c',sku='G11111111',list_price=10000,ask=1000,fit=True)]))
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertEqual([s['name'] for s in allshops[:2]],['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_listing_io_drift_keeps_a_original_objects_and_records_every_sent_attempt(self):
        from types import SimpleNamespace
        path=Path(self.md)/'shops.json'
        path.write_text(json.dumps([dict(name=s['name'],url=s['url'],reviews=s['reviews'],
            offers=s['usable']) for s in self.shops]))
        self.order['_stock_shops_path']=str(path)
        self.order['_stock_all_shop_names']=[s['name'] for s in self.shops]
        self.enable()
        a_shops=copy.deepcopy(self.shops)
        a_order={k:v for k,v in self.order.items() if k!=trial.KEY}
        a_order['order']=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='A')
        trial.assign(self.md,a_order)
        a_early,_=mb.prefetch_pool(a_shops,{'trades':[]},self.md,'fixture',3,kind='goods',
            order=a_order,budget=10000,chance_of=lambda sku:.1)
        early,quotable=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        self.rows.append(dict(ts=NOW,kind='goods',category='fixture',quotes=[dict(
            shop='c',sku='G11111111',list_price=10000,ask=1000,fit=True)]))
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        # Only quote targets are intercepted; listing IO threads really start and are joined.
        a_sent=self.dispatch(a_early,order=a_order)
        sent=self.dispatch(early)
        self.assertGreater(self.dispatch_io_count,0)
        self.assertEqual(a_sent,['a','c'])
        self.assertEqual(sent,a_sent)
        originals={s['name']:s for s in self.shops}
        self.assertTrue(all(s is originals[s['name']] for s in early))
        self.assertEqual({s['name'] for s in self.shops if s.get('_quote_attempted')},set(sent))
        self.assertEqual([s['name'] for s in quotable[:2]],sent)
        rec=mb.order_record(self.order,SimpleNamespace(budget=10000,item='goods'),self.shops,{},
            'a','choice',self.shops[0],'chosen',self.shops[0]['usable'][0],6000,.1,
            dict(state='NO_VALUE',history=[]),None,dict(receipt=None),[],[],False,None,{}, {})
        self.assertEqual({q['shop'] for q in rec['quotes']},set(sent))
        self.assertEqual(len(rec['quotes']),2)
        self.assertTrue(all(q['sku']=='G11111111' and q['list_price']==10000 for q in rec['quotes']))
        self.assertTrue(rec['quote_attempts_complete'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_expiry_between_thread_starts_keeps_fresh_slot_and_no_extra_dispatch(self):
        self.enable(); early,allshops=self.prefetch()
        early_ref,quotable_ref=early,allshops
        sent=self.dispatch(early,after_start=lambda s:setattr(self.clock,'return_value',self.cfg['end']))
        self.assertEqual(sent,['d','c'])
        self.assertTrue(trial.measurement(self.order)['applied'])
        # Review N1: publish the successful requests, not a fully unsent baseline prefix.
        self.assertIs(early,early_ref)
        self.assertIs(allshops,quotable_ref)
        self.assertEqual([s['name'] for s in early],sent)
        self.assertEqual([s['name'] for s in allshops[:2]],sent)
        self.assertEqual(set(self.dispatch_threads),set(sent))
        self.assertEqual(len(sent),len(set(sent)))
        got={name:dict(ask=1000,text='') for name in sent}
        self.assertFalse(mb.quotes_enough(early,got,10000,{},self.order))  # two asks do not reach the unchanged three

    def test_a_arm_exact_baseline_and_off_metadata_zero_bytes(self):
        before=json.dumps(self.rows[0])
        self.assertEqual(json.dumps(dict(self.rows[0],**trial.fields(self.order))),before)
        self.order['order']=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='A')
        self.enable(); early,allshops=self.prefetch()
        self.assertEqual([s['name'] for s in allshops],['a','c','b','d','e'])
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_b1_factor_rival_and_fresh_slot_use_entire_pool(self):
        self.enable()
        with patch.object(mb.buyer_b3b1,'enabled',return_value=True), \
             patch.object(mb.buyer_b3b1,'paid_rates',return_value=({'d':0,'e':0},1)):
            early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_rival_stays_last_even_with_highest_yield(self):
        self.enable(); early,allshops=self.prefetch(rival={'d'})
        self.assertNotIn('d',[s['name'] for s in early])
        self.assertEqual(allshops[-1]['name'],'d')

    def test_attempt_denominator_rejects_bad_asks_and_uses_current_short_warranty_cap(self):
        self.enable()
        # Valid historical fit flags cannot make zero-warranty quotes valuable now.
        self.order['_sw']=dict(S=.32,hist={'d':0},zero_hist=['d'])
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_recorded_quote_warranty_text_uses_existing_live_cap_parser(self):
        self.enable()
        self.order['_sw']=dict(S=.32,hist={})
        self.rows[-1]['quotes'][0]['text']='No breakage warranty offered.'
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])

    def test_failed_attempts_are_zero_reward_and_bool_nonfinite_badtype_asks_rejected(self):
        self.enable()
        for r in self.rows[:2]: r['quotes'][0]['ask']=4000
        self.rows.extend(dict(ts=NOW-1,kind='goods',category='fixture',quotes=[dict(
            shop='d',sku='G11111111',list_price=10000,ask=a,fit=False,lost=lost)])
            for a,lost in [(None,'no ask')]*20+[(True,None),(float('nan'),None),(float('inf'),None),('1000',None),(100,'refused')])
        (Path(self.md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])

    def test_frozen_b27_selected_sku_does_not_learn_another_sku(self):
        self.enable()
        for s in self.shops:
            s['usable'].append(dict(s['usable'][0],sku='G22222222',list_price=8000))
        mb.buyer_goods_risk_sku30.reserve_default(self.order,self.shops,10000,mb.pick_offer,
            lambda sku:.2 if sku=='G11111111' else .1)
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertEqual({s['_offer']['sku'] for s in early},{'G22222222'})
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_assignment_duplicate_is_unknown_and_snapshot_has_no_raw_inputs(self):
        self.enable()
        duplicate={k:v for k,v in self.order.items() if k!=trial.KEY}
        trial.assign(self.md,duplicate)
        self.assertEqual(trial.measurement(duplicate)['trace'],'unknown')
        docs=[json.loads(p.read_text()) for p in Path(self.md).glob(trial.PREFIX+'*.json')]
        self.assertEqual(len(docs),1)
        self.assertNotIn('salt',json.dumps(docs))
        self.assertNotIn('https:',json.dumps(docs))

    def test_active_other_finite_trial_at_dispatch_skips_only_new_b(self):
        self.enable(); early,_=self.prefetch()
        path=Path(self.md)/mb.buyer_goods_risk_sku30.SWITCH
        data=json.dumps(self.cfg).encode();path.write_bytes(data);os.utime(path,(NOW-10,NOW-10))
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])
        self.assertEqual(path.read_bytes(),data)

    def test_eight_same_order_callers_get_one_immutable_assignment_and_io_failure_is_unknown(self):
        import concurrent.futures, threading
        self.enable()
        original={k:v for k,v in self.order.items() if k!=trial.KEY}
        original['order']=next(str(i) for i in range(200,300) if trial.arm(self.cfg,str(i))=='B')
        barrier=threading.Barrier(8)
        def invoke(i):
            order=dict(original);barrier.wait();trial.assign(self.md,order)
            return trial.measurement(order)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            notes=list(pool.map(invoke,range(8)))
        self.assertEqual(sum(n.get('trace')!='unknown' for n in notes),1)
        self.assertTrue(all(not n['applied'] for n in notes))
        original['order']='io-fixture'
        with patch.object(trial.os,'mkdir',side_effect=OSError('fixture IO')):
            trial.assign(self.md,original)
        self.assertEqual(trial.measurement(original)['trace'],'unknown')

    def test_actual_main_early_give_up_assigns_itt_but_never_treats_and_sanitizes_input(self):
        import contextlib, io
        from types import SimpleNamespace
        self.enable(); self.order.pop(trial.KEY)
        # Use another immutable order; the first one is already reserved by enable().
        self.order['order']=next(str(i) for i in range(100,200) if trial.arm(self.cfg,str(i))=='B')
        p=Path(self.md)
        (p/'shops.json').write_text(json.dumps([dict(s,offers=s['usable'],**{trial.KEY:{'applied':True}}) for s in self.shops]))
        (p/'catalog.json').write_text(json.dumps({'G11111111':dict(self.shops[0]['usable'][0],kind='goods')}))
        (p/'order.json').write_text(json.dumps(dict(self.order,acceptable=['G11111111'],round=3,
            **{trial.KEY:{'note':{'arm':'B','applied':True}}})))
        argv=['market_buyer.py','--shops',str(p/'shops.json'),'--item','fixture','--budget','10000','--order',str(p/'order.json')]
        with patch('sys.argv',argv),patch.object(mb.httpx,'get',return_value=SimpleNamespace(json=lambda:{'round':3})), \
             patch.object(mb,'warm_ledger'),patch.object(mb,'ledger_breaks',return_value={'sold':0,'rate':.1,'by_sku':{}}), \
             patch.object(mb,'hopeless',return_value=True),patch.object(mb,'ledger_rows',return_value=[]), \
             patch.object(mb,'MEMORY',str(p/'memory.json')),patch.object(mb,'AGENT_DIR',self.md), \
             patch.object(mb,'SAVED_SHARE',mb.SAVED_MIN_SHARE), \
             patch.object(mb,'buyer_negotiate',side_effect=AssertionError('no talk')), \
             patch.object(mb,'start_quotes',side_effect=AssertionError('no dispatch')),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mb.main(),0)
        row=json.loads((p/'measure'/'orders.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['state'],'NO_VALUE')
        self.assertEqual(row['goods_quote_yield30']['arm'],'B')
        self.assertFalse(row['goods_quote_yield30']['applied'])

    def test_actual_order_serializer_off_zero_delta_and_one_compact_note(self):
        from types import SimpleNamespace
        s=dict(self.shops[0],_offer=self.shops[0]['usable'][0],_b=.1,_quote_attempted=True)
        def record():
            return mb.order_record(self.order,SimpleNamespace(budget=10000,item='goods'),[s],{},
                s['name'],'choice',s,'chosen',s['_offer'],6000,.1,dict(state='NO_VALUE',history=[]),
                None,dict(receipt=None),[],[],False,None,{}, {})
        before=record()
        trial.assign(self.md,self.order)
        off=record()
        self.assertEqual(json.dumps(before),json.dumps(off))
        self.enable(); active=record()
        delta=len(json.dumps(active).encode())-len(json.dumps(off).encode())
        self.assertGreater(delta,0); self.assertLessEqual(delta,180)
        self.assertEqual(json.dumps(active).count('goods_quote_yield30'),1)
        self.assertTrue(all('goods_quote_yield30' not in q for q in active['quotes']))

    def test_invalid_caps_are_not_hidden_by_minimum_and_never_rewarded(self):
        self.enable()
        with patch.object(mb,'short_cap',return_value=float('nan')):
            early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['a','c'])
        self.assertEqual(self.dispatch(early),['a','c'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_changed_source_shape_order_or_switch_reverts_without_dispatch_mark(self):
        for change in ('shape','url','order','switch','slots','source'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as md:
                oldmd=self.md; self.md=md
                try:
                    (Path(md)/'measure').mkdir()
                    (Path(md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
                    self.order.pop(trial.KEY,None)
                    if change=='source':
                        path=Path(md)/'shops.json'
                        path.write_text(json.dumps(self.shops))
                        self.order['_stock_shops_path']=str(path)
                        self.order['_stock_all_shop_names']=[s['name'] for s in self.shops]
                    self.enable(); early,allshops=self.prefetch()
                    if change=='shape': self.shops[0]['usable'][0]['qty']=2
                    if change=='url': self.shops[0]['url']='https://changed.example/rpc'
                    if change=='order': self.order['order']='wrong'
                    if change=='switch': (Path(md)/trial.SWITCH).write_text('{}')
                    if change=='slots': self.order['_stock_k']=4
                    if change=='source': path.write_text(json.dumps(self.shops[:-1]))
                    self.assertFalse('d' in self.dispatch(early))
                    self.assertFalse(trial.measurement(self.order)['applied'])
                    self.assertEqual([s['name'] for s in allshops[:2]],['a','c'])
                finally:
                    self.md=oldmd
                    self.order.pop('_stock_shops_path',None)
                    self.order.pop('_stock_all_shop_names',None)


class SharedLearning(unittest.TestCase):
    def test_same_cohort_in_all_five_consumers_retains_a_untreated_and_normal_b27(self):
        import buyer_rank_pg as rp
        import buyer_shop_profile as sp
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row
        marks=[None,dict(arm='A',applied=True),dict(arm='B',applied=False),dict(arm='B',applied=True)]
        rows=[]
        for i,mark in enumerate(marks):
            r=row('fixture','goods',11000,[10800,10900,10900,11000][i],ts=NOW-10,order=str(i))
            r.update(limit=10000, asks=[11000,11000])
            if mark: r['goods_quote_yield30']=mark
            rows.append(r)
        rows[0]['goods_risk_sku30']=None  # normal default b27 has no trial mark
        retained=rows[:3]
        self.assertEqual(mb.priced(rows),mb.priced(retained))
        self.assertEqual(len(mb.priced(rows)),3)
        expected=rp.stats_from_rows(retained,{},NOW)
        actual=rp.stats_from_rows(rows,{},NOW)
        for key in ('pool','shops','nofit'): self.assertEqual(actual[key],expected[key])
        self.assertEqual(actual['pool']['goods']['n'],3)
        # Same rows, repeated with distinct order IDs, make the profile threshold observable.
        expanded=[dict(r,order=f'{r["order"]}-{i}') for i in range(40) for r in rows]
        kept=[r for r in expanded if not trial.applied_b(r)]
        self.assertEqual(sp.profile_from_rows(kept,{},NOW),{'fixture':109})
        self.assertEqual(sp.profile_from_rows(expanded,{},NOW),sp.profile_from_rows(kept,{},NOW))
        with tempfile.TemporaryDirectory() as md:
            (Path(md)/'measure').mkdir()
            (Path(md)/'measure'/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            (Path(md)/'payments.jsonl').write_text(''.join(json.dumps(dict(order=str(i),paid=i!=3))+'\n' for i in range(4)))
            self.assertEqual(mb.recent_orders(md,NOW,3),retained)
            self.assertEqual(b1.paid_rates(md,now=NOW),({'fixture':1.0},1.0))


class SwitchContract(unittest.TestCase):
    def test_exact_future_window_missing_malformed_before_start_and_at_end_baseline(self):
        with tempfile.TemporaryDirectory() as md, patch.object(trial.time,'time',return_value=NOW-10):
            self.assertIsNone(trial.config(md))
            for start,end in [(True,NOW+1799),(float('nan'),NOW+1799),(NOW-1,NOW+1800),(NOW-1,float('inf'))]:
                with self.assertRaises(ValueError): trial.switch_on(md,study='fixture',salt='fixture',start=start,end=end)
            trial.switch_on(md,study='fixture',salt='fixture',start=NOW-1,end=NOW+1799)
            with self.assertRaises(FileExistsError): trial.switch_on(md,study='fixture',salt='fixture',start=NOW-1,end=NOW+1799)
            os.utime(Path(md)/trial.SWITCH,(NOW-10,NOW-10))
            self.assertIsNone(trial.config(md,NOW-2))
            self.assertIsNotNone(trial.config(md,NOW))
            self.assertIsNone(trial.config(md,NOW+1799))
            for raw in ('[]','{}','bad',json.dumps(dict(enabled=True,study='fixture',salt='fixture',start=True,end=NOW+1799))):
                (Path(md)/trial.SWITCH).write_text(raw)
                self.assertIsNone(trial.config(md,NOW))


if __name__=='__main__': unittest.main()
