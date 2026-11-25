"""Goods-only risk SKU choice: finite dispatch and original negotiation guards."""
import unittest
import copy
import collections
import math
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import buyer_goods_risk_sku30 as trial

class RiskSkuCandidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.md=self.tmp.name
        self.cfg=dict(enabled=True,study='risk-test',salt='risk-test',start=2000000000,end=2000001800)
        self.high=dict(sku='G11111111',name='fixture high',list_price=10000,qty=1)
        self.low=dict(sku='G22222222',name='fixture low',list_price=8000,qty=1)
        self.shops=[dict(name='fixture',url='fixture',usable=[self.high,self.low])]
        self.oid=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='B')
        self.order=dict(kind='goods',order=self.oid,_buyer_started_at=2000000001)
        self.base=lambda offers,budget:max(offers,key=lambda o:o['list_price'])
        self.risk=lambda sku:.15 if sku==self.high['sku'] else .09

    def flag(self,**changes):
        p=Path(self.md)/trial.SWITCH;p.write_text(json.dumps(self.cfg|changes))
        os.utime(p,(1999999990,1999999990))

    def reserve(self,order=None,shops=None,**kwargs):
        return trial.reserve(self.md,self.order if order is None else order,
            self.shops if shops is None else shops,12000,self.base,self.risk,now=2000000001,**kwargs)

    def test_off_and_food_do_not_scan_models_or_create_journal_or_log_fields(self):
        for kind in ('goods','food'):
            order=dict(self.order,kind=kind)
            with patch.object(trial,'candidate',side_effect=AssertionError('OFF model call')):
                self.assertIsNone(self.reserve(order=order))
            self.assertIsNone(trial.measurement(order))
        self.assertEqual(list(Path(self.md).iterdir()),[])
        self.flag()
        self.assertIsNone(self.reserve(order=dict(self.order,kind='food')))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_generator_and_reader_enforce_exact_future_window(self):
        with patch.object(trial.time,'time',return_value=1999999990):
            for change in ({'end':2000003600},{'start':float('nan')},{'end':float('inf')},{'start':True}):
                cfg={k:self.cfg[k] for k in ('study','salt','start','end')}|change
                with self.assertRaises(ValueError):trial.switch_on(self.md,**cfg)
            trial.switch_on(self.md,**{k:self.cfg[k] for k in ('study','salt','start','end')})
        p=Path(self.md)/trial.SWITCH;os.utime(p,(1999999990,1999999990))
        self.assertIsNone(trial.config(self.md,1999999999))
        self.assertIsNotNone(trial.config(self.md,2000000000))
        self.assertIsNone(trial.config(self.md,2000001800))

    def test_reader_rejects_nan_inf_bool_bad_shape_and_wrong_duration(self):
        for change in ({'start':float('nan')},{'end':float('inf')},{'enabled':1},{'start':True},
                       {'end':2000001801},{'study':'../bad'},{'salt':None}):
            self.flag(**change)
            self.assertIsNone(trial.config(self.md,2000000001),change)

    def test_preexisting_started_window_and_other_trials_fail_closed(self):
        self.flag()
        for value in (1999999999,2000001800,float('nan'),float('inf'),None,True):
            self.assertIsNone(self.reserve(order=dict(self.order,_buyer_started_at=value)))
        for key in ('_goods_take30','_stock_evidence','_goods_extra_quote_probe',
                    '_quote_alignment','_our_ask','_moss_first'):
            self.assertIsNone(self.reserve(order=dict(self.order,**{key:{'arm':'B'}})))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_changed_inventory_or_switched_off_before_dispatch_keeps_baseline(self):
        self.flag()
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            self.shops[0]['usable'].append(dict(self.low,sku='G33333333'))
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.high)
            self.assertFalse(trial.measurement(self.order)['applied'])
        # No new B on an order already seen, including a failed partial snapshot.
        self.assertIsNone(self.reserve(order=dict(self.order)))
        self.assertEqual(self.order[trial.KEY]['arm'],'B')  # retry dict did not mutate first assignment

    def test_disabled_between_plan_and_dispatch_and_end_boundary_never_treat(self):
        for at,disable in ((2000001800,False),(2000000001,True)):
            with tempfile.TemporaryDirectory() as md:
                cfg=self.cfg
                p=Path(md)/trial.SWITCH;p.write_text(json.dumps(cfg));os.utime(p,(1999999990,1999999990))
                shops=copy.deepcopy(self.shops);order=dict(self.order)
                trial.reserve(md,order,shops,12000,self.base,self.risk,now=2000000001)
                if disable:p.write_text(json.dumps(cfg|{'enabled':False}))
                with patch.object(trial.time,'time',return_value=at):
                    self.assertEqual(trial.dispatch(shops[0],12000,self.base),self.high)
                self.assertFalse(trial.measurement(order)['applied'])

    def test_unknown_overbudget_outofstock_and_equal_or_worse_risk_never_replace(self):
        for offers,chance,budget in (([self.high,self.low],lambda sku:None,12000),
                    ([self.high,self.low],lambda sku:.15,12000),
                    ([self.high,self.low],lambda sku:.15 if sku==self.high['sku'] else .2,12000),
                    ([self.high,dict(self.low,qty=0)],self.risk,12000),
                    ([self.high,self.low],self.risk,7000)):
            self.assertIsNone(trial.candidate(offers,self.high,budget,chance))

    def test_a_assignment_and_no_alternative_are_untreated(self):
        self.flag()
        oid=next(str(i) for i in range(100) if trial.arm(self.cfg,str(i))=='A')
        order=dict(self.order,order=oid)
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve(order=order)
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.high)
        self.assertEqual(trial.measurement(order)['arm'],'A')
        self.assertFalse(trial.measurement(order)['applied'])

    def test_actual_b_excluded_from_rank_profile_and_paid_rates(self):
        import buyer_rank_pg as rp
        import buyer_shop_profile as sp
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row, NOW
        from test_buyer_shop_profile import holder
        base=row('fixture','goods',10000,8000,order='base')
        a=dict(row('fixture','goods',10000,9000,order='a'),
               goods_risk_sku30=dict(arm='A',applied=True))
        untreated=dict(row('fixture','goods',10000,order='u'),
               goods_risk_sku30=dict(arm='B',applied=False))
        treated=dict(row('fixture','goods',10000,order='b'),
               goods_risk_sku30=dict(arm='B',applied=True))
        paid={'base':'confirmed','a':'confirmed'}
        expected=rp.stats_from_rows([base,a,untreated],paid,NOW)
        actual=rp.stats_from_rows([base,a,untreated,treated],paid,NOW)
        self.assertEqual(expected['pool']['goods']['n'],3)
        self.assertAlmostEqual(expected['pool']['goods']['p'],2/3)
        for field in ('pool','shops','nofit'):self.assertEqual(actual[field],expected[field])
        self.assertEqual(actual['counts']['excluded_rows'],expected['counts']['excluded_rows']+1)
        from test_buyer_shop_profile import NOW as profile_now
        rows=holder('H',[1.08]*20)
        pollution=[dict(r,goods_risk_sku30=dict(arm='B',applied=True)) for r in holder('H',[1.3]*100)]
        self.assertEqual(sp.profile_from_rows(rows,{},profile_now),{'H':108})
        self.assertEqual(sp.profile_from_rows(rows+pollution,{},profile_now),{'H':108})
        with tempfile.TemporaryDirectory() as md:
            p=Path(md)/'measure';p.mkdir()
            (p/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [base,a,dict(untreated,state='TASK_STATE_COMPLETED',price=8000,passed_to_kit=True),
                    dict(treated,state='TASK_STATE_COMPLETED',price=8000,passed_to_kit=True)]))
            (Path(md)/'payments.jsonl').write_text(''.join(json.dumps(dict(order=k,paid=v))+'\n'
                  for k,v in [('base',True),('a',True),('u',False),('b',False)]))
            rates,prior=b1.paid_rates(md,now=NOW)
            self.assertAlmostEqual(prior,2/3)

    def test_new_selection_uses_original_main_ladder_wire_and_alternative_receipt_cap(self):
        import ast
        import market_buyer as mb
        import buyer
        from types import SimpleNamespace
        from test_buyer_reply import task
        tree=ast.parse(Path(mb.__file__).read_text())
        ladder=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='price_ladder')
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call)
                  and isinstance(n.value.func,ast.Name) and n.value.func.id=='negotiate')
        self.flag()
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            offer=trial.dispatch(self.shops[0],12000,self.base)
        self.assertEqual(offer['sku'],'G22222222')
        text='fixture low (G22222222). Asking 4000. Includes a 6-hour warranty.'
        shop=self.shops[0];shop['_offer']=offer
        q=dict(ask=4000,ask_src='metadata',text=text,url='fixture',task=task(text,4000))
        limit=mb.saved_cap(offer,.09,share=.32)
        self.assertEqual(limit,5186)
        env=dict(mb.__dict__,md=self.md,order=self.order,offer=offer,q=q,steps=(.9,),
                 shop=shop,key=offer['sku'],label='fixture low',limit=limit,top=limit,note='',r3=True,
                 resume=('fixture',q['task']),probe_arm=None,discount_arm=None,goods_first_arm=None,
                 args=SimpleNamespace(budget=12000))
        exec(compile(ast.fix_missing_locations(ast.Module([ladder],type_ignores=[])),'<real-ladder>','exec'),env)
        with patch.object(buyer,'send',return_value=task('',state='TASK_STATE_COMPLETED')) as send, \
             patch.object(buyer,'rpc',return_value=None),patch.object(buyer,'log'), \
             patch.object(buyer,'decide',side_effect=AssertionError('code-priced, no model')):
            exec(compile(ast.fix_missing_locations(ast.Module([call],type_ignores=[])),'<real-main-wire>','exec'),env)
        self.assertEqual([c.args[1] for c in send.call_args_list],['3600'])
        self.assertIsNotNone(mb.over_limit({'item':'G22222222','price':5500},offer,limit,{},12000))
        self.assertIsNone(mb.warranty_short({'warranty_hours':6},dict(kind='goods')))
        self.assertIsNotNone(mb.warranty_short({'warranty_hours':0},dict(kind='goods')))

    def test_serialized_order_has_zero_off_delta_and_only_one_small_active_note(self):
        import market_buyer as mb
        from types import SimpleNamespace
        shop=dict(self.shops[0],_offer=self.high,_b=.15)
        def record(order):
            return mb.order_record(order,SimpleNamespace(budget=12000,item='goods'),
                 [shop],{},shop['name'],'choice',shop,'chosen',self.high,6000,.15,
                 dict(state='NO_VALUE',history=[]),None,dict(receipt=None),[],[],False,None,{}, {})
        with patch.object(mb,'buyer_cfg',return_value={}),patch.object(mb.time,'time',return_value=2000000001):
            before=record(self.order)
            self.assertIsNone(self.reserve())
            off=record(self.order)
            self.assertEqual(json.dumps(before,sort_keys=True),json.dumps(off,sort_keys=True))
            self.assertNotIn('goods_risk_sku30',off)
            self.flag();self.reserve()
            active=record(self.order)
        extra=len((json.dumps(active,ensure_ascii=False)+'\n').encode())-len((json.dumps(off,ensure_ascii=False)+'\n').encode())
        self.assertGreater(extra,0);self.assertLessEqual(extra,150)
        self.assertEqual(json.dumps(active).count('goods_risk_sku30'),1)
        self.assertTrue(all('goods_risk_sku30' not in q for q in active['quotes']))

    def test_atomic_seen_allows_one_concurrent_selection_without_waiting(self):
        import concurrent.futures
        self.flag()
        def invoke(i):
            order=copy.deepcopy(self.order);shops=copy.deepcopy(self.shops)
            return self.reserve(order=order,shops=shops)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(invoke,range(8)))
        self.assertEqual(sum(r is not None for r in results),1)
        self.assertEqual(len(list(Path(self.md).glob(trial.PREFIX+'*.json'))),1)

    def test_oversize_integer_switch_fails_closed_without_crashing_main(self):
        self.flag(start=10**400,end=10**400+1800)
        self.assertIsNone(trial.config(self.md,2000000001))
        with patch.object(trial.time,'time',return_value=1999999990):
            with self.assertRaises(ValueError):
                trial.switch_on(self.md,study='fixture',salt='fixture',start=10**400,end=10**400+1800)

    def test_existing_price_trials_are_excluded_before_selection_but_not_read_off(self):
        self.assertIsNone(self.reserve(conflict=lambda: (_ for _ in ()).throw(AssertionError('OFF conflict lookup'))))
        self.flag()
        self.assertIsNone(self.reserve(conflict=lambda:True))
        self.assertIsNone(trial.measurement(self.order))
        self.assertEqual(len(list(Path(self.md).iterdir())),1)

    def test_I1_planning_hopeless_and_prefetch_never_mark_B_as_dispatched(self):
        import market_buyer as mb
        self.flag()
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            self.assertEqual(mb.selected_offer(self.shops[0],12000),self.low)
            self.assertFalse(trial.measurement(self.order)['applied'])
            self.assertFalse(mb.hopeless(self.shops,12000,{},{}))
            with patch.object(mb,'recent_fits',return_value=collections.Counter()),patch.object(mb.buyer_b3b1,'enabled',return_value=False), \
                 patch.object(mb.buyer_stock_evidence,'decide',return_value=None):
                mb.prefetch_pool(self.shops,{'trades':[]},self.md,'fixture',2,kind='goods',
                                 order=self.order,budget=12000)
            self.assertFalse(trial.measurement(self.order)['applied'])
            self.assertFalse(trial.applied_b({'goods_risk_sku30':trial.measurement(self.order)}))
        with patch.object(trial.time,'time',return_value=self.cfg['end']):
            sent=[]
            with patch.object(mb,'quote',side_effect=lambda s,label,sku,*a:sent.append(sku)):
                threads=mb.start_quotes(self.shops,self.order,12000,self.risk,{},mb.quote_ctl())
                for th in threads.values():th.join(1)
            self.assertEqual(sent,['G11111111'])
            self.assertFalse(trial.measurement(self.order)['applied'])

    def test_I1_actual_main_gave_up_keeps_B_untreated_and_no_new_dispatch(self):
        import contextlib,io
        import market_buyer as mb
        self.flag()
        p=Path(self.md)
        (p/'shops.json').write_text(json.dumps([dict(name='fixture',url='fixture',offers=[self.high,self.low],reviews=[])]))
        (p/'catalog.json').write_text(json.dumps({o['sku']:dict(o,kind='goods') for o in [self.high,self.low]}))
        (p/'order.json').write_text(json.dumps(self.order|dict(acceptable=[self.high['sku'],self.low['sku']],round=3,category='fixture')))
        argv=['market_buyer.py','--shops',str(p/'shops.json'),'--item','fixture','--budget','12000','--order',str(p/'order.json')]
        with patch('sys.argv',argv),patch.object(mb.time,'time',return_value=2000000001), \
             patch.object(mb.httpx,'get',return_value=SimpleNamespace(json=lambda:{'round':3})), \
             patch.object(mb,'warm_ledger'),patch.object(mb,'ledger_breaks',return_value={'sold':100,'rate':.1,
                    'by_sku':{'G11111111':{'sold':80,'broke':12},'G22222222':{'sold':20,'broke':1}}}), \
             patch.object(mb.market_model,'break_chance',side_effect=lambda stats,sku:self.risk(sku)), \
             patch.object(trial,'reserve_default',side_effect=lambda order,shops,budget,baseline,chance,**kw:
                 trial.reserve(self.md,order,shops,budget,baseline,chance,**kw)), \
             patch.object(mb,'hopeless',return_value=True),patch.object(mb,'ledger_rows',return_value=[]), \
             patch.object(mb,'MEMORY',str(p/'memory.json')),patch.object(mb,'AGENT_DIR',self.md), \
             patch.object(mb,'SAVED_SHARE',mb.SAVED_MIN_SHARE),patch.object(mb,'buyer_negotiate',side_effect=AssertionError('no talk')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mb.main(),0)
        row=json.loads((p/'measure/orders.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['state'],'NO_VALUE')
        self.assertFalse(row['goods_risk_sku30']['applied'])
        self.assertFalse(trial.applied_b(row))

    def test_I1_final_direct_main_offer_locks_then_actual_protocol_marks_B(self):
        import ast
        import buyer
        import market_buyer as mb
        from types import SimpleNamespace
        from test_buyer_reply import task
        self.flag()
        tree=ast.parse(Path(mb.__file__).read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        index=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign) and
                   any(isinstance(t,ast.Name) and t.id=='offer' for t in n.targets))
        final_selection=main.body[index-1:index+1]  # actual preparation statement and original final-offer seam
        with patch.object(trial.time,'time',return_value=2000000001):
            self.reserve()
            env=dict(mb.__dict__,shop=self.shops[0],args=SimpleNamespace(budget=12000),r3=True,gave_up=False)
            exec(compile(ast.fix_missing_locations(ast.Module(final_selection,type_ignores=[])),'<actual-final-offer>','exec'),env)
            chosen=env['offer']
            self.assertEqual(chosen['sku'],'G22222222')
            self.assertEqual(self.shops[0][trial.KEY]['locked'],'G22222222')
            self.assertFalse(trial.measurement(self.order)['applied'],'selection preparation is not dispatch')
            text='fixture low (G22222222). Asking 4000. Includes a 6-hour warranty.'
            replies=iter([task(text,4000),task('',state='TASK_STATE_COMPLETED')])
            with patch.object(buyer.httpx,'get',return_value=SimpleNamespace(json=lambda:
                    {'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'fixture'}]})), \
                 patch.object(buyer,'send',side_effect=lambda *a,**k:next(replies)) as send, \
                 patch.object(buyer,'rpc',return_value=None),patch.object(buyer,'log'), \
                 patch.object(buyer,'decide',side_effect=AssertionError('no model')):
                mb.negotiate('fixture','fixture low',5186,'',mb.request_text('fixture low',chosen['sku'],self.order),
                   goods_take_context=(self.md,self.order,chosen,{},(.9,),self.shops[0],chosen['sku']),
                   ladder=lambda meta,text,history:dict(action='offer',price=3600,shop_price=4000),take_at=None)
            self.assertIn('G22222222',send.call_args_list[0].args[1])
            self.assertEqual(send.call_args_list[1].args[1],'3600')
            self.assertTrue(trial.measurement(self.order)['applied'])

    def test_prior_only_alternative_is_kept_and_snapshot_records_sample_counts(self):
        import market_model
        self.flag()
        stats=dict(sold=100,broke=10,rate=.1,by_sku={'G11111111':dict(sold=80,broke=12)})
        with patch.object(trial.time,'time',return_value=2000000001):
            trial.reserve(self.md,self.order,self.shops,12000,self.base,
                          lambda sku:market_model.break_chance(stats,sku),stats=lambda:stats)
        snapshot=json.loads(next(Path(self.md).glob(trial.PREFIX+'*.json')).read_text())
        entry=snapshot['choices']['fixture']
        self.assertEqual(entry['candidate'],'G22222222','keep existing prior, never a new min-sold gate')
        self.assertEqual(entry['baseline_evidence']['sold'],80)
        self.assertEqual(entry['baseline_evidence']['broke'],12)
        self.assertEqual(entry['candidate_evidence']['sold'],0)
        self.assertEqual(entry['candidate_evidence']['broke'],0)
        self.assertTrue(entry['candidate_evidence']['prior_only'])
        self.assertEqual(entry['candidate_evidence']['prior_weight'],20)
        self.assertEqual(entry['candidate_evidence']['prior_rate'],.1)
        self.assertNotIn('salt',snapshot['config'])
        self.assertFalse(trial.measurement(self.order)['applied'])

    def test_existing_break_model_can_choose_lower_risk_acceptable_sku(self):
        high = dict(sku='G11111111', name='fixture high', list_price=10000, qty=1)
        low = dict(sku='G22222222', name='fixture low', list_price=8000, qty=1)
        unknown = dict(sku='G33333333', name='fixture unknown', list_price=7000, qty=1)
        offer = trial.candidate([high, unknown, low], high, 12000,
                                lambda sku: {'G11111111': .15, 'G22222222': .09}.get(sku))
        self.assertEqual(offer, low)
        self.assertEqual(high['sku'], 'G11111111')

    def test_finite_dispatch_locks_same_sku_for_final_talk_but_never_starts_after_end(self):
        with tempfile.TemporaryDirectory() as md:
            cfg = dict(enabled=True, study='risk-test', salt='risk-test', start=2000000000, end=2000001800)
            p = Path(md)/trial.SWITCH
            p.write_text(json.dumps(cfg)); os.utime(p, (1999999990,1999999990))
            high = dict(sku='G11111111', list_price=10000, qty=1)
            low = dict(sku='G22222222', list_price=8000, qty=1)
            shops = [dict(name='fixture',url='fixture',usable=[high,low])]
            oid = next(str(i) for i in range(100) if trial.arm(cfg,str(i))=='B')
            order = dict(kind='goods',order=oid,_buyer_started_at=2000000001)
            baseline = lambda offers,budget: max(offers,key=lambda o:o['list_price'])
            with patch.object(trial.time,'time',return_value=2000000001):
                trial.reserve(md,order,shops,12000,baseline,lambda sku: .15 if sku==high['sku'] else .09)
                self.assertEqual(trial.dispatch(shops[0],12000,baseline), low)
                self.assertTrue(trial.measurement(order)['applied'])
            with patch.object(trial.time,'time',return_value=cfg['end']):
                self.assertEqual(trial.offer(shops[0],12000,baseline), low, 'a sent quote must keep its SKU')
                fresh = dict(name='fixture',url='fixture',usable=[high,low])
                expired = dict(order,order='fresh')
                self.assertIsNone(trial.reserve(md,expired,[fresh],12000,baseline,lambda sku:.01))
                self.assertEqual(trial.dispatch(fresh,12000,baseline),high)

    def test_real_quote_dispatch_and_final_offer_use_same_alternative_sku(self):
        import market_buyer as mb
        high = dict(sku='G11111111', name='fixture high', list_price=10000, qty=1)
        low = dict(sku='G22222222', name='fixture low', list_price=8000, qty=1)
        with tempfile.TemporaryDirectory() as md:
            cfg = dict(enabled=True,study='risk-test',salt='risk-test',start=2000000000,end=2000001800)
            p = Path(md)/trial.SWITCH;p.write_text(json.dumps(cfg));os.utime(p,(1999999990,1999999990))
            oid=next(str(i) for i in range(100) if trial.arm(cfg,str(i))=='B')
            order=dict(kind='goods',order=oid,_buyer_started_at=2000000001)
            shop=dict(name='fixture',url='fixture',usable=[high,low])
            sent=[]
            def quote(s,label,sku,order,got,ctl):
                sent.append((label,sku))
            with patch.object(trial.time,'time',return_value=2000000001), patch.object(mb,'quote',quote):
                trial.reserve(md,order,[shop],12000,mb.pick_offer,lambda sku:.15 if sku==high['sku'] else .09)
                threads=mb.start_quotes([shop],order,12000,lambda sku:.09,{},mb.quote_ctl())
                for th in threads.values():th.join(1)
                self.assertEqual(sent,[('fixture low','G22222222')])
            with patch.object(trial.time,'time',return_value=cfg['end']):
                self.assertEqual(mb.selected_offer(shop,12000),low)
                self.assertEqual(shop['_offer'],low)

    def test_actual_b_price_and_quote_learning_are_excluded_but_a_and_unapplied_stay(self):
        import market_buyer as mb
        rows=[dict(order=str(i),kind='goods',ts=2000000001,
                   goods_risk_sku30=dict(study='risk-test',arm=a,applied=applied))
              for i,(a,applied) in enumerate([('B',True),('A',True),('B',False)])]
        self.assertEqual([r['order'] for r in mb.priced(rows)],['1','2'])
        with tempfile.TemporaryDirectory() as md:
            p=Path(md)/'measure';p.mkdir()
            (p/'orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.assertEqual([r['order'] for r in mb.recent_orders(md,2000000002,3)],['1','2'])


class DefaultRiskSkuTest(unittest.TestCase):
    setUp = RiskSkuCandidateTest.setUp
    # Existing finite regression methods remain owned by RiskSkuCandidateTest.
    def test_default_without_trial_reader_selects_and_locks_without_order_bytes(self):
        before=json.dumps(self.order,sort_keys=True)
        with patch.object(trial,'config',side_effect=AssertionError('no finite reader')), \
             patch.object(trial,'arm',side_effect=AssertionError('no assignment')):
            trial.reserve_default(self.order,self.shops,12000,self.base,self.risk)
            self.assertEqual(trial.offer(self.shops[0],12000,self.base),self.low)
            self.assertEqual(trial.prepare_final(self.shops[0],12000,self.base),self.low)
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.low)
        self.assertEqual(json.dumps(self.order,sort_keys=True),before)
        self.assertIsNone(trial.measurement(self.order))
        self.assertFalse(trial.applied_b(self.order))
        self.assertEqual(list(Path(self.md).iterdir()),[])

    def reserve(self, order=None, shops=None, chance=None, **kwargs):
        return trial.reserve_default(self.order if order is None else order,
            self.shops if shops is None else shops,12000,self.base,chance or self.risk,**kwargs)

    def test_default_unknown_invalid_equal_and_ambiguous_inventory_keep_baseline(self):
        for risk in (None,True,float('nan'),float('inf'),-1,1.1,.09):
            with self.subTest(risk=risk):
                shop=copy.deepcopy(self.shops[0])
                self.reserve(shops=[shop],chance=lambda sku:risk if sku==self.high['sku'] else .09)
                self.assertEqual(trial.dispatch(shop,12000,self.base),self.high)
        for extra in (dict(self.low),dict(self.high,list_price=6000)):
            shop=copy.deepcopy(self.shops[0]);shop['usable'].append(extra)
            self.reserve(shops=[shop])
            self.assertEqual(trial.dispatch(shop,12000,self.base),self.high)

    def test_default_ties_keep_original_order_and_ignores_ineligible_candidates(self):
        other=dict(self.low,sku='G33333333',name='other',list_price=7000)
        unavailable=dict(self.low,sku='G44444444',qty=0)
        over=dict(self.low,sku='G55555555',list_price=13000)
        shop=dict(self.shops[0],usable=[self.high,other,self.low,unavailable,over])
        base=lambda offers,budget:max((o for o in offers if o['list_price']<=budget),key=lambda o:o['list_price'])
        risk=lambda sku:{self.high['sku']:.15,other['sku']:.09,self.low['sku']:.09,
                         unavailable['sku']:.01,over['sku']:.01}[sku]
        trial.reserve_default(self.order,[shop],12000,base,risk)
        self.assertEqual(trial.dispatch(shop,12000,base),other)

    def test_default_food_and_conflict_guards_do_not_evaluate_risks(self):
        def forbidden(sku):raise AssertionError('guard must precede estimator')
        orders=[dict(self.order,kind='food')]+[dict(self.order,**{k:{'arm':'B'}})
            for k in ('_goods_take30','_stock_evidence','_goods_extra_quote_probe',
                      '_quote_alignment','_our_ask','_moss_first')]
        for order in orders:
            self.reserve(order=order,chance=forbidden)
            self.assertNotIn(trial.KEY,self.shops[0])
        for conflict in (lambda:True,lambda:(_ for _ in ()).throw(ValueError('unknown'))):
            self.reserve(chance=forbidden,conflict=conflict)
            self.assertNotIn(trial.KEY,self.shops[0])

    def test_default_preview_rechecks_shape_url_and_baseline_before_lock(self):
        for change in ('url','shape','budget'):
            shop=copy.deepcopy(self.shops[0]);self.reserve(shops=[shop])
            if change=='url':shop['url']='changed'
            if change=='shape':shop['usable'][1]['qty']=2
            budget=7000 if change=='budget' else 12000
            self.assertEqual(trial.offer(shop,budget,self.base),self.high)
            self.assertNotIn('locked',shop[trial.KEY])

    def test_default_prior_only_uses_existing_break_estimator(self):
        import market_model
        stats=dict(sold=100,broke=10,rate=.1,by_sku={self.high['sku']:dict(sold=80,broke=12)})
        self.reserve(chance=lambda sku:market_model.break_chance(stats,sku))
        self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),self.low)

    def test_default_planning_prefetch_and_dispatch_leave_historical_B_note_untouched(self):
        import market_buyer as mb
        old=dict(study='historical',arm='B',applied=False)
        self.order[trial.KEY]=old
        self.reserve()
        self.assertEqual(mb.selected_offer(self.shops[0],12000),self.low)
        self.assertNotIn('locked',self.shops[0][trial.KEY])
        self.assertFalse(mb.hopeless(self.shops,12000,{},{}))
        with patch.object(mb,'recent_fits',return_value=collections.Counter()), \
             patch.object(mb.buyer_b3b1,'enabled',return_value=False), \
             patch.object(mb.buyer_stock_evidence,'decide',return_value=None):
            mb.prefetch_pool(self.shops,{'trades':[]},self.md,'fixture',2,kind='goods',
                             order=self.order,budget=12000)
        trial.prepare_final(self.shops[0],12000,self.base)
        trial.dispatch(self.shops[0],12000,self.base)
        self.assertEqual(old,dict(study='historical',arm='B',applied=False))
        self.assertFalse(trial.applied_b({'goods_risk_sku30':old}))

    def test_default_quote_final_lock_keeps_label_cap_and_copied_offer(self):
        import market_buyer as mb
        self.reserve()
        sent=[]
        with patch.object(mb,'quote',side_effect=lambda s,label,sku,*a:sent.append((label,sku))):
            threads=mb.start_quotes(self.shops,self.order,12000,self.risk,{},mb.quote_ctl())
            for th in threads.values():th.join(1)
        self.assertEqual(sent,[('fixture low','G22222222')])
        self.shops[0]['usable'][1]['name']='mutated listing'
        final=trial.prepare_final(self.shops[0],12000,self.base)
        self.assertEqual((final['name'],final['sku']),('fixture low','G22222222'))
        self.assertEqual(mb.saved_cap(final,.09,share=.32),5186)
        self.assertIsNotNone(mb.over_limit({'item':final['sku'],'price':5500},final,5186,{},12000))
        self.assertIsNone(trial.measurement(self.order))

    def test_default_new_order_serialization_has_zero_trial_bytes(self):
        import market_buyer as mb
        from types import SimpleNamespace
        shop=self.shops[0];shop.update(_offer=self.low,_b=.09)
        def record():
            return mb.order_record(self.order,SimpleNamespace(budget=12000,item='goods'),
                [shop],{},shop['name'],'choice',shop,'chosen',self.low,5186,.09,
                dict(state='NO_VALUE',history=[]),None,dict(receipt=None),[],[],False,None,{}, {})
        with patch.object(mb,'buyer_cfg',return_value={}),patch.object(mb.time,'time',return_value=2000000001):
            before=json.dumps(record(),ensure_ascii=False,sort_keys=True).encode()
            self.reserve();trial.dispatch(shop,12000,self.base)
            after=json.dumps(record(),ensure_ascii=False,sort_keys=True).encode()
        self.assertEqual(len(after)-len(before),0)
        self.assertEqual(after,before)
        self.assertNotIn(b'goods_risk_sku30',after)

    def test_default_rows_enter_all_five_learning_consumers_and_old_finite_B_does_not(self):
        import market_buyer as mb
        import buyer_rank_pg as rp
        import buyer_shop_profile as sp
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row, NOW
        from test_buyer_shop_profile import holder, NOW as profile_now
        self.reserve();trial.dispatch(self.shops[0],12000,self.base)
        default=row('fixture','goods',10000,8000,order='default')
        # The normal serializer adds no trial field. The historical field keeps its original meaning.
        note=trial.measurement(self.order)
        if note is not None:default['goods_risk_sku30']=note
        old=dict(default,order='old',goods_risk_sku30=dict(arm='B',applied=True))
        self.assertEqual(mb.priced([default,old]),[default])
        expected=rp.stats_from_rows([default],{'default':'confirmed'},NOW)
        actual=rp.stats_from_rows([default,old],{'default':'confirmed','old':'refused'},NOW)
        self.assertEqual(actual['pool'],expected['pool'])
        self.assertEqual(actual['pool']['goods']['n'],1)
        rows=holder('H',[1.08]*sp.MIN_TALKS)
        if note is not None:
            rows=[dict(r,goods_risk_sku30=note) for r in rows]
        oldrows=[dict(r,goods_risk_sku30=dict(arm='B',applied=True)) for r in holder('H',[1.3]*100)]
        self.assertEqual(sp.profile_from_rows(rows+oldrows,{},profile_now),{'H':108})
        p=Path(self.md);(p/'measure').mkdir()
        (p/'measure/orders.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in [default,old]))
        (p/'payments.jsonl').write_text(json.dumps(dict(order='default',paid=True))+'\n'+json.dumps(dict(order='old',paid=False))+'\n')
        self.assertEqual(mb.recent_orders(self.md,NOW,3),[default])
        self.assertEqual(b1.paid_rates(self.md,now=NOW)[1],1)

    def test_default_actual_main_NO_VALUE_has_no_trial_order_or_memory_fields(self):
        import contextlib,io
        import market_buyer as mb
        p=Path(self.md)
        # Imported shop/order context is untrusted; actual main must sanitize it.
        poison=dict(mode='default',locked=self.high['sku'],locked_offer=self.high)
        (p/'shops.json').write_text(json.dumps([dict(name='fixture',url='fixture',offers=[self.high,self.low],
             reviews=[],**{trial.KEY:poison})]))
        (p/'catalog.json').write_text(json.dumps({o['sku']:dict(o,kind='goods') for o in [self.high,self.low]}))
        (p/'order.json').write_text(json.dumps(self.order|dict(acceptable=[self.high['sku'],self.low['sku']],
             round=3,category='fixture',**{trial.KEY:dict(arm='B',applied=True)})))
        argv=['market_buyer.py','--shops',str(p/'shops.json'),'--item','fixture','--budget','12000','--order',str(p/'order.json')]
        with patch('sys.argv',argv),patch.object(mb.time,'time',return_value=2000000001), \
             patch.object(mb.httpx,'get',return_value=SimpleNamespace(json=lambda:{'round':3})), \
             patch.object(trial,'config',side_effect=AssertionError('default cannot read finite config')), \
             patch.object(trial,'arm',side_effect=AssertionError('default cannot assign AB')), \
             patch.object(mb,'warm_ledger'),patch.object(mb,'ledger_breaks',return_value={'sold':100,'rate':.1,
                    'by_sku':{'G11111111':{'sold':80,'broke':12},'G22222222':{'sold':20,'broke':1}}}), \
             patch.object(mb.market_model,'break_chance',side_effect=lambda stats,sku:self.risk(sku)), \
             patch.object(mb,'hopeless',return_value=True),patch.object(mb,'ledger_rows',return_value=[]), \
             patch.object(mb,'MEMORY',str(p/'memory.json')),patch.object(mb,'AGENT_DIR',self.md), \
             patch.object(mb,'SAVED_SHARE',mb.SAVED_MIN_SHARE), \
             patch.object(mb,'buyer_negotiate',side_effect=AssertionError('NO_VALUE must not talk')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mb.main(),0)
        row=json.loads((p/'measure/orders.jsonl').read_text().splitlines()[-1])
        trade=json.loads((p/'memory.json').read_text())['trades'][-1]
        self.assertEqual(row['state'],'NO_VALUE')
        self.assertEqual(trade['item'],'G22222222')
        for serialized in (row,trade):
            self.assertNotIn('goods_risk_sku30',serialized)
            self.assertNotIn(trial.KEY,serialized)
            self.assertFalse(trial.applied_b(serialized))

    def test_default_actual_final_caller_uses_locked_SKU_and_protocol_without_trial_mark(self):
        import ast
        import buyer
        import market_buyer as mb
        from types import SimpleNamespace
        from test_buyer_reply import task
        self.reserve()
        tree=ast.parse(Path(mb.__file__).read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        index=next(i for i,n in enumerate(main.body) if isinstance(n,ast.Assign) and
                   any(isinstance(t,ast.Name) and t.id=='offer' for t in n.targets))
        env=dict(mb.__dict__,shop=self.shops[0],args=SimpleNamespace(budget=12000),r3=True,gave_up=False)
        exec(compile(ast.fix_missing_locations(ast.Module(main.body[index-1:index+4],type_ignores=[])),
                     '<default-actual-final-offer>','exec'),env)
        chosen=env['offer'];limit=mb.saved_cap(chosen,.09,share=.32)
        self.assertEqual((env['key'],env['label'],limit),('G22222222','fixture low',5186))
        self.assertIsNone(trial.measurement(self.order))
        replies=iter([task('fixture low (G22222222). Asking 4000. Includes a 6-hour warranty.',4000),
                      task('',state='TASK_STATE_COMPLETED')])
        with patch.object(buyer.httpx,'get',return_value=SimpleNamespace(json=lambda:
                {'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'fixture'}]})), \
             patch.object(buyer,'send',side_effect=lambda *a,**k:next(replies)) as send, \
             patch.object(buyer,'rpc',return_value=None),patch.object(buyer,'log'), \
             patch.object(buyer,'decide',side_effect=AssertionError('code pricing only')):
            mb.negotiate('fixture',env['label'],limit,'',mb.request_text(env['label'],env['key'],self.order),
                goods_take_context=(self.md,self.order,chosen,{},(.9,),self.shops[0],env['key']),
                ladder=lambda meta,text,history:dict(action='offer',price=3600,shop_price=4000),take_at=None)
        self.assertIn('G22222222',send.call_args_list[0].args[1])
        self.assertEqual(send.call_args_list[1].args[1],'3600')
        self.assertIsNone(trial.measurement(self.order))
        self.assertEqual(trial.offer(self.shops[0],12000,self.base),self.low)

    def test_default_lowest_risk_wins_and_expired_trial_file_cannot_disable_it(self):
        best=dict(self.low,sku='G33333333',list_price=7000,name='least risk')
        self.shops[0]['usable'].append(best)
        (Path(self.md)/trial.SWITCH).write_text(json.dumps(dict(enabled=True,study='expired',salt='expired',start=1,end=1801)))
        with patch.object(trial,'config',side_effect=AssertionError('expired trial must be irrelevant')):
            self.reserve(chance=lambda sku:{self.high['sku']:.15,self.low['sku']:.09,best['sku']:.05}[sku])
            self.assertEqual(trial.dispatch(self.shops[0],12000,self.base),best)

    def test_default_preview_detects_changed_baseline_and_reservation_is_atomic_for_ambiguous_shops(self):
        other=dict(self.low,sku='G33333333',list_price=7000)
        shop=copy.deepcopy(self.shops[0]);shop['usable'].append(other)
        self.reserve(shops=[shop],chance=lambda sku:.09 if sku==self.low['sku'] else .15)
        self.assertEqual(trial.offer(shop,12000,lambda offers,budget:offers[2]),other)
        first=copy.deepcopy(self.shops[0]);duplicate=copy.deepcopy(first)
        self.reserve(shops=[first,duplicate])
        self.assertNotIn(trial.KEY,first)
        self.assertNotIn(trial.KEY,duplicate)
