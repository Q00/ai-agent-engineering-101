"""Finite opening inquiry: actual caller and transport fixture, never real RPC."""
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import market_buyer as mb
import test_buyer_goods_quote_yield30 as prior


class QuoteInquiry(unittest.TestCase):
    setUp = prior.QuoteYieldDispatch.setUp
    prefetch = prior.QuoteYieldDispatch.prefetch

    def enable_inquiry(self):
        self.cfg['study']='inquiry-test'
        self.cfg['salt']='inquiry-test'
        self.order['order']=next(str(i) for i in range(100) if mb.buyer_goods_risk_sku30.arm(self.cfg,str(i))=='B')
        p=Path(self.md)/'buyer_goods_quote_inquiry30.json'
        p.write_text(json.dumps(self.cfg));os.utime(p,(prior.NOW-10,prior.NOW-10))

    def test_actual_main_give_up_assigns_itt_and_rejects_injected_treatment(self):
        self.enable_inquiry();p=Path(self.md)
        (p/'shops.json').write_text(json.dumps([dict(s,offers=s['usable']) for s in self.shops]))
        (p/'catalog.json').write_text(json.dumps({'G11111111':dict(self.shops[0]['usable'][0],kind='goods')}))
        (p/'order.json').write_text(json.dumps(dict(self.order,acceptable=['G11111111'],round=3,
            _goods_quote_inquiry30={'note':{'arm':'B','applied':True}})))
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
        self.assertIn('goods_quote_inquiry30',row)
        self.assertEqual(row['goods_quote_inquiry30']['arm'],'B')
        self.assertFalse(row['goods_quote_inquiry30']['applied'])

    def dispatch_requests(self, early, *, before_run=None, fail=None, response=None, chance=.1):
        sent=[];real=mb.threading.Thread;io_threads=[];attempts=[]
        card={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'https://fixture.example/rpc'}]}
        def send(url,text,*args,**kwargs):
            sent.append(text)
            return response if response is not None else {'id':'fixture-task','status':{'state':'TASK_STATE_INPUT_REQUIRED',
                'message':{'parts':[{'text':'Price 4800 with a 6 h warranty'}],'metadata':{'ask':4800}}}}
        class QuoteThread:
            def __init__(self,target,args):self.target=target;self.args=args
            def start(self):
                if self.args[0]['name']==fail:raise RuntimeError('fixture start failure')
                attempts.append(self.args[0]['name'])
                if before_run:before_run(self.args[0])
                self.target(*self.args)
        def thread(target,args=(),**kw):
            if target is mb.quote:return QuoteThread(target,args)
            t=real(target=target,args=args,**kw);io_threads.append(t);return t
        self.got={}
        with patch.object(mb.threading,'Thread',thread),patch.object(mb.httpx,'get',
                return_value=SimpleNamespace(status_code=200,json=lambda:card)),patch.object(mb,'buyer_send',send):
            try:mb.start_quotes(early,self.order,10000,lambda sku:chance,self.got,mb.quote_ctl())
            except RuntimeError:
                if fail is None:raise
        for t in io_threads:t.join(1);self.assertFalse(t.is_alive())
        self.io_count=len(io_threads)
        self.started=attempts
        return sent

    def test_actual_prefetch_thread_quote_send_requests_current_cap_without_accepting(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        early,quotable=self.prefetch();baseline=list(early)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        sent=self.dispatch_requests(early)
        self.assertEqual(self.started,['a','c'])
        self.assertTrue(all('at most 6444' in text for text in sent))
        self.assertTrue(all('not an offer or acceptance' in text for text in sent))
        self.assertTrue(all('6 h warranty' in text and 'G11111111' in text for text in sent))
        self.assertEqual(early,baseline)
        self.assertEqual([s['name'] for s in quotable[:2]],self.started)
        self.assertTrue(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        self.assertTrue(all(q['ask']==4800 for q in self.got.values()))

    def test_same_actual_treated_cohort_is_excluded_by_all_five_learners(self):
        import buyer_goods_quote_inquiry30 as policy
        import buyer_rank_pg as rank
        import buyer_shop_profile as profile
        import buyer_b3b1 as b1
        from test_buyer_rank_pg import row
        self.enable_inquiry();policy.assign(self.md,self.order)
        early,_=self.prefetch();self.dispatch_requests(early)
        base=dict(row('fixture','goods',11000,10900,ts=prior.NOW,order='normal'),limit=10000,asks=[11000,11000])
        treated=dict(base,order='treated',**policy.fields(self.order))
        a=dict(base,order='A',goods_quote_inquiry30=dict(arm='A',applied=False))
        b=dict(base,order='untreated',goods_quote_inquiry30=dict(arm='B',applied=False))
        old=dict(base,order='old',goods_quote_yield30=dict(arm='B',applied=True))
        cohort=[base,a,b,treated,old]
        self.assertEqual(mb.priced(cohort),[base,a,b])
        p=Path(self.md)/'measure'/'orders.jsonl';p.write_text(''.join(json.dumps(r)+'\n' for r in cohort))
        self.assertEqual(mb.recent_orders(self.md,prior.NOW,3),[base,a,b])
        self.assertEqual(rank.stats_from_rows(cohort,{},prior.NOW)['pool']['goods']['n'],3)
        expanded=[dict(r,order=f'{r["order"]}-{i}',price=11000 if r in [treated,old] else 10900)
            for r in cohort for i in range(40)]
        self.assertEqual(profile.profile_from_rows(expanded,{},prior.NOW),{'fixture':109})
        (Path(self.md)/'payments.jsonl').write_text(''.join(json.dumps(dict(order=r['order'],paid=r not in [treated,old]))+'\n' for r in cohort))
        self.assertEqual(b1.paid_rates(self.md,now=prior.NOW)[1],1.0)

    def test_expiry_after_plan_before_quote_sends_exact_original_and_not_applied(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        sent=self.dispatch_requests(early,before_run=lambda s:setattr(self.clock,'return_value',self.cfg['end']))
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_kill_between_requests_keeps_same_slots_and_marks_only_first_changed_request(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order);early,quotable=self.prefetch()
        def kill(s):
            if s['name']=='c':(Path(self.md)/policy.SWITCH).write_text('{"enabled":false}')
        sent=self.dispatch_requests(early,before_run=kill)
        self.assertIn('at most 6444',sent[0])
        self.assertEqual(sent[1],mb.request_text('fixture','G11111111',self.order))
        self.assertEqual(self.started,['a','c'])
        self.assertEqual([s['name'] for s in early],self.started)
        self.assertEqual([s['name'] for s in quotable[:2]],self.started)
        self.assertEqual(policy.fields(self.order)['goods_quote_inquiry30']['requests'],1)

    def test_listing_real_io_and_source_drift_keep_original_objects_and_records(self):
        import buyer_goods_quote_inquiry30 as policy
        p=Path(self.md)/'shops.json'
        listing=[dict(name=s['name'],url=s['url'],reviews=s['reviews'],offers=s['usable']) for s in self.shops]
        p.write_text(json.dumps(listing))
        self.order.update(_stock_shops_path=str(p),_stock_all_shop_names=[s['name'] for s in self.shops])
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        def drift(s):
            if s['name']=='a':p.write_text(json.dumps(listing)+'\n')
            else:setattr(self.clock,'return_value',self.cfg['end'])
        sent=self.dispatch_requests(early,before_run=drift)
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertTrue(all(any(s is orig for orig in self.shops) for s in early))
        self.assertEqual({s['name'] for s in self.shops if s.get('_quote_attempted')},{'a','c'})
        self.assertGreater(self.io_count,0)
        rec=self.record()
        self.assertEqual({q['shop'] for q in rec['quotes']},{'a','c'})
        self.assertTrue(rec['quote_attempts_complete'])
        self.assertFalse(rec['goods_quote_inquiry30']['applied'])

    def record(self):
        s=self.shops[0]
        return mb.order_record(self.order,SimpleNamespace(budget=10000,item='fixture'),self.shops,getattr(self,'got',{}),
            'a','choice',s,'chosen',s.get('_offer') or s['usable'][0],6000,.1,dict(state='NO_VALUE',history=[]),
            None,dict(receipt=None),[],[],False,None,{}, {})

    def test_thread_failure_never_treats_and_off_serialization_is_zero_bytes(self):
        import buyer_goods_quote_inquiry30 as policy
        baseline=json.dumps(self.record(),sort_keys=True)
        policy.assign(self.md,self.order)
        self.assertEqual(json.dumps(self.record(),sort_keys=True),baseline)
        self.assertEqual(list(Path(self.md).glob(policy.PREFIX+'*')),[])
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        self.assertEqual(self.dispatch_requests(early,fail='a'),[])
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        rec=self.record();self.assertEqual(json.dumps(rec).count('goods_quote_inquiry30'),1)
        self.assertTrue(all('goods_quote_inquiry30' not in q for q in rec['quotes']))

    def test_a_and_food_are_exact_original_and_no_food_assignment(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry()
        self.order['order']=next(str(i) for i in range(100) if policy.arm(self.cfg,str(i))=='A')
        policy.assign(self.md,self.order);early,_=self.prefetch()
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        food=dict(kind='food',order='food',_buyer_started_at=prior.NOW)
        policy.assign(self.md,food);self.assertEqual(policy.fields(food),{})

    def test_invalid_caps_fail_closed_and_existing_warranty_refusal_is_preserved(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        early,_=self.prefetch()
        s=self.shops[0];o=s['usable'][0];s.update(_offer=o,_b=.1)
        for value in (True,float('nan'),float('inf'),'6444',None,-1,0,10001):
            policy.prepare(self.order,s,o,10000,lambda:value,lambda:False)
        self.assertEqual(list(Path(self.md).glob(policy.PREFIX+'*.plan.json')),[])
        refused={'id':'refused','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{
            'parts':[{'text':'Price 1000, no warranty'}],'metadata':{'ask':1000}}}}
        self.dispatch_requests(early,response=refused)
        self.assertTrue(all(q['refused'] for q in self.got.values()))
        self.assertFalse(mb.quotes_enough(early,self.got,10000,mb.buyer_cfg(),self.order))

    def test_completed_response_is_not_a_quote_or_payment_claim(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        self.dispatch_requests(early,response={'id':'done','status':{'state':'TASK_STATE_COMPLETED'}})
        self.assertTrue(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        self.assertTrue(all(q['ask'] is None for q in self.got.values()))
        self.assertFalse(self.record()['passed_to_kit'])

    def test_duplicate_assignment_and_changed_identity_fail_closed(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        other=dict(self.order);other.pop(policy.KEY);policy.assign(self.md,other)
        self.assertEqual(policy.fields(other)['goods_quote_inquiry30']['trace'],'unknown')
        early,_=self.prefetch()
        sent=self.dispatch_requests(early,before_run=lambda s:self.order.update(order='wrong-order'))
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_existing_goods_deadline_prevents_treatment(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        self.order['_quote_deadline']={'at':prior.NOW+1,'seconds':13.0}
        early,_=self.prefetch()
        sent=self.dispatch_requests(early,before_run=lambda s:setattr(self.clock,'return_value',prior.NOW+1))
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_unknown_break_input_cannot_authorize_a_new_inquiry(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        sent=self.dispatch_requests(early,chance=None)
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_source_drift_between_prefetch_and_start_is_baseline_inquiry(self):
        import buyer_goods_quote_inquiry30 as policy
        p=Path(self.md)/'shops.json'
        p.write_text(json.dumps([dict(name=s['name'],url=s['url'],reviews=s['reviews'],offers=s['usable']) for s in self.shops]))
        self.order.update(_stock_shops_path=str(p),_stock_all_shop_names=[s['name'] for s in self.shops])
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        p.write_text(p.read_text()+'\n')
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_missing_malformed_prestart_atend_and_future_helper_never_extend(self):
        import buyer_goods_quote_inquiry30 as policy
        for i,cfg in enumerate((None,[],{'enabled':True},dict(self.cfg,enabled=1),
                dict(self.cfg,start=prior.NOW+1,end=prior.NOW+1801),
                dict(self.cfg,start=prior.NOW-1800,end=prior.NOW),dict(self.cfg,end=float('inf')))):
            md=Path(self.md)/f'cfg-{i}';md.mkdir()
            if cfg is not None:
                p=md/policy.SWITCH;p.write_text(json.dumps(cfg));os.utime(p,(prior.NOW-2000,prior.NOW-2000))
            order=dict(kind='goods',order='invalid',_buyer_started_at=prior.NOW)
            policy.assign(str(md),order);self.assertEqual(policy.fields(order),{})
            self.assertEqual(list(md.glob(policy.PREFIX+'*')),[])
        md=Path(self.md)/'operator';md.mkdir()
        policy.switch_on(str(md),study='future-test',salt='future-test',start=prior.NOW+1,end=prior.NOW+1801)
        original=(md/policy.SWITCH).read_bytes()
        with self.assertRaises(FileExistsError):
            policy.switch_on(str(md),study='future-test',salt='future-test',start=prior.NOW+1,end=prior.NOW+1801)
        self.assertEqual((md/policy.SWITCH).read_bytes(),original)

    def test_active_old_finite_policy_skips_new_request_without_changing_old_flag(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        p=Path(self.md)/mb.buyer_goods_risk_sku30.SWITCH
        p.write_text(json.dumps(self.cfg));os.utime(p,(prior.NOW-10,prior.NOW-10));before=p.read_bytes()
        early,_=self.prefetch()
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertEqual(p.read_bytes(),before)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_assignment_byte_change_cannot_authorize_changed_request(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order);early,_=self.prefetch()
        assignment=next(p for p in Path(self.md).glob(policy.PREFIX+'*.json') if '.plan.' not in p.name)
        assignment.write_bytes(assignment.read_bytes()+b'\n')
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_controller_io_error_after_assignment_is_unknown_and_baseline(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        p=Path(self.md)/policy.SWITCH;p.rename(p.with_suffix('.backup'));p.mkdir()
        early,_=self.prefetch()
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertEqual(policy.fields(self.order)['goods_quote_inquiry30']['trace'],'unknown')

    def test_ambiguous_pool_url_cannot_authorize_a_changed_inquiry(self):
        import buyer_goods_quote_inquiry30 as policy
        self.enable_inquiry();policy.assign(self.md,self.order)
        self.shops[1]['url']=self.shops[0]['url']
        early,_=self.prefetch()
        self.assertEqual(self.dispatch_requests(early),[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def normal_runtime_warranty(self):
        """Real normal readers; keep this separate from historical finite OFF fixtures."""
        (Path(self.md)/'buyer_goods_quote_yield.json').write_text('{"enabled":true}')
        (Path(self.md)/'buyer_short_warranty.json').write_text('{"enabled":true}')
        self.order['_sw']=mb.buyer_short_warranty.state(self.md,self.order,now=prior.NOW,env={})
        self.addCleanup(patch.stopall)
        patch.dict(mb._SW_CTX,{},clear=True).start()
        self.assertIsNotNone(self.order['_sw'])

    def test_current_normal_warranty_and_yield_reach_actual_inquiry_with_same_full_cap(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        mb.buyer_goods_quote_yield30.assign(self.md,self.order)
        policy.assign(self.md,self.order)
        early,quotable=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        baseline=list(early)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        sent=self.dispatch_requests(early)
        self.assertEqual(self.started,['d','c'])
        self.assertEqual(len(sent),2)
        self.assertTrue(all(text.startswith(mb.request_text('fixture','G11111111',self.order)) for text in sent))
        self.assertTrue(all('at most 6444' in text for text in sent))
        self.assertTrue(all('not an offer or acceptance' in text for text in sent))
        self.assertTrue(all(s is baseline[i] for i,s in enumerate(early)))
        self.assertEqual(quotable[:2],early)
        self.assertTrue(all(q['ask']==4800 for q in self.got.values()))
        self.assertTrue(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        self.assertFalse(self.record()['passed_to_kit'])

    def test_current_warranty_unknown_state_is_baseline_with_bounded_reason(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        self.order['_sw']['S']=True
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch();sent=self.dispatch_requests(early)
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        note=policy.fields(self.order)['goods_quote_inquiry30']
        self.assertFalse(note['applied'])
        self.assertEqual(note['skip'],'warranty_state_unknown')

    def test_current_positive_short_cap_uses_existing_worst_sku_catalog_guard(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        self.order['_sw']['hist']={'d':3,'c':3}
        self.order['acceptable']=['G11111111','G22222222']
        mb._SW_CTX.update(md=self.md,catalog={'G11111111':dict(kind='goods',list_price=10000),
            'G22222222':dict(kind='goods',list_price=5000)})
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch()
        self.assertEqual([s['name'] for s in early],['d','c'])
        sent=self.dispatch_requests(early)
        # Original worst-SKU/grade fallback b=.0837, S=.30, h=3: cap3134, not own-SKU6444.
        self.assertTrue(all('at most 3134' in text for text in sent))
        self.assertTrue(all(mb.short_cap(s['_offer'],s['_b'],self.order,s['name'],{},s)==3134 for s in early))
        self.assertTrue(all(s['_offer']['sku']=='G11111111' and s['_offer']['list_price']==10000 for s in early))
        self.assertTrue(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        self.assertTrue(all(q['ask']==4800 and not q['refused'] for q in self.got.values()))
        # A full-warranty reply does not erase the same existing short-history price guard.
        self.assertTrue(all(q['fit'] is False for q in self.record()['quotes']))

    def test_current_known_zero_stays_baseline_and_no_warranty_response_is_refused(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        self.order['_sw'].update(hist={s['name']:0 for s in self.shops},
            zero_hist=[s['name'] for s in self.shops])
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch()
        reply={'id':'zero','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{
            'parts':[{'text':'Price 1000, no warranty'}],'metadata':{'ask':1000}}}}
        sent=self.dispatch_requests(early,response=reply)
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertTrue(all(mb.short_cap(s['_offer'],s['_b'],self.order,s['name'],{},s)==0 for s in early))
        self.assertTrue(all(q['refused'] for q in self.got.values()))
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
        self.assertEqual(policy.fields(self.order)['goods_quote_inquiry30']['skip'],'zero_warranty')
        self.assertFalse(self.record()['passed_to_kit'])

    def test_current_short_history_with_known_positive_warranty_is_not_blanket_zero(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        # main records every short shop at hist0 but records actual no-warranty shops separately.
        self.order['_sw'].update(hist={'d':0,'c':0},zero_hist=[])
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch();sent=self.dispatch_requests(early)
        self.assertTrue(all('at most 5555' in text for text in sent))
        self.assertTrue(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_current_warranty_frame_drift_after_plan_keeps_exact_baseline_message(self):
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch()
        sent=self.dispatch_requests(early,before_run=lambda s:self.order['_sw'].update(S=.31))
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])

    def test_current_normal_a_b_keep_same_stock_objects_slots_sku_limits_and_prefix(self):
        import buyer_goods_quote_inquiry30 as policy
        results=[]
        for arm in ('A','B'):
            case=QuoteInquiry();case.setUp()
            try:
                case.normal_runtime_warranty();case.enable_inquiry()
                case.order['order']=next(str(i) for i in range(100) if policy.arm(case.cfg,str(i))==arm)
                mb.buyer_goods_quote_yield30.assign(case.md,case.order);policy.assign(case.md,case.order)
                case.order['_quote_deadline']={'at':prior.NOW+13,'seconds':13.0}
                early,quotable=case.prefetch();originals=list(early);sent=case.dispatch_requests(early)
                self.assertTrue(all(s is originals[i] for i,s in enumerate(early)))
                prefix=mb.request_text('fixture','G11111111',case.order)
                self.assertTrue(all(text.startswith(prefix) for text in sent))
                results.append(dict(names=case.started,quotable=[s['name'] for s in quotable],
                    offers=[(s['_offer']['sku'],s['_offer']['list_price'],mb.quote_inquiry_cap(s,s['_offer'],case.order,10000)) for s in early],
                    attempted=[s['name'] for s in case.shops if s.get('_quote_attempted')],deadline=case.order['_quote_deadline']))
                note=policy.fields(case.order)['goods_quote_inquiry30']
                self.assertEqual(note['applied'],arm=='B')
                self.assertEqual(sum('Quote inquiry only' in text for text in sent),2 if arm=='B' else 0)
            finally:case.doCleanups()
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[0]['names'],['d','c'])

    def test_current_normal_actual_b_retains_same_five_consumer_learning_contract(self):
        self.normal_runtime_warranty();self.enable_inquiry()
        mb.buyer_goods_quote_yield30.assign(self.md,self.order)
        self.test_same_actual_treated_cohort_is_excluded_by_all_five_learners()

    def test_current_normal_expiry_kill_and_deadline_are_exact_baseline_inquiries(self):
        import buyer_goods_quote_inquiry30 as policy
        for boundary in ('expiry','kill','deadline'):
            case=QuoteInquiry();case.setUp()
            try:
                case.normal_runtime_warranty();case.enable_inquiry()
                mb.buyer_goods_quote_yield30.assign(case.md,case.order);policy.assign(case.md,case.order)
                case.order['_quote_deadline']={'at':prior.NOW+13,'seconds':13.0}
                early,_=case.prefetch()
                def stop(shop):
                    if boundary=='kill':(Path(case.md)/policy.SWITCH).write_text('{"enabled":false}')
                    else:case.clock.return_value=case.cfg['end'] if boundary=='expiry' else prior.NOW+13
                sent=case.dispatch_requests(early,before_run=stop)
                self.assertEqual(sent,[mb.request_text('fixture','G11111111',case.order)]*2)
                self.assertEqual(case.started,['d','c'])
                self.assertFalse(policy.fields(case.order)['goods_quote_inquiry30']['applied'])
            finally:case.doCleanups()

    def test_current_sender_rechecks_expiry_after_real_assignment_io_validation(self):
        import builtins
        import buyer_goods_quote_inquiry30 as policy
        self.normal_runtime_warranty();self.enable_inquiry()
        mb.buyer_goods_quote_yield30.assign(self.md,self.order);policy.assign(self.md,self.order)
        early,_=self.prefetch()
        assignment=next(p for p in Path(self.md).glob(policy.PREFIX+'*.json') if '.plan.' not in p.name)
        real_open=builtins.open;reads=[];clock=self.clock;end=self.cfg['end']
        class SlowRead:
            def __init__(self,src):self.src=src
            def __enter__(self):self.src.__enter__();return self
            def __exit__(self,*args):return self.src.__exit__(*args)
            def read(self,*args):
                data=self.src.read(*args);reads.append(1)
                if len(reads)==2:clock.return_value=end  # prepare read1, sender validation read2
                return data
        def open_file(path,*args,**kw):
            src=real_open(path,*args,**kw)
            return SlowRead(src) if isinstance(path,(str,Path)) and Path(path)==assignment else src
        with patch.object(builtins,'open',open_file):sent=self.dispatch_requests(early)
        self.assertEqual(len(reads),2)
        self.assertEqual(sent,[mb.request_text('fixture','G11111111',self.order)]*2)
        self.assertFalse(policy.fields(self.order)['goods_quote_inquiry30']['applied'])
