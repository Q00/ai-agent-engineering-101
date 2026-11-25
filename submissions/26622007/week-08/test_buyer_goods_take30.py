"""Finite cheap metadata goods take: allocation and final wire, never a market call."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import buyer
import buyer_goods_take30 as gt
from test_buyer_reply import task

class GoodsTake30Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name
        self.now = 2000000001
        self.cfg = dict(enabled=True, study='goods-take-test', salt='test', start=2000000000, end=2000001800)
        self.flag()
        self.order = dict(kind='goods', order='o0', _buyer_started_at=self.now)
        # Find B deterministically, no product arm override.
        self.order['order'] = next('o'+str(i) for i in range(100) if gt.arm(self.cfg, 'o'+str(i)) == 'B')
        self.offer = dict(sku='G12345678', name='fixture goods', list_price=10000)
        self.text = 'fixture goods (G12345678). Asking 4000. Includes a 6-hour warranty.'
        self.q = dict(ask=4000, ask_src='metadata', text=self.text, url='fixture',
                      task=task(self.text, 4000))
        self.shop = dict(name='fixture-shop')
    def flag(self, **changes):
        p = Path(self.md)/gt.SWITCH
        p.write_text(json.dumps(self.cfg | changes))
        os.utime(p, (1999999990, 1999999990))
    def assign(self):
        return gt.assign(self.md, self.order, now=self.now, env={})
    def run_wire(self, *, stopped=None, repeat=False, unknown=False, expire=False, **changes):
        self.assign()
        q = changes.pop('q', self.q)
        order = changes.pop('order', self.order)
        sent = []
        def send(url, value, tid=None):
            sent.append(value)
            if unknown: raise buyer.RpcReplyError({'method':'SendMessage'})
            return task(self.text, 4000) if repeat else task('', state='TASK_STATE_COMPLETED')
        def ladder(meta, text, history):
            return {'shop_price':meta, 'action':'offer' if not history else 'walk_away', 'price':3600}
        def guard(d, h):
            if expire: self.flag(enabled=False)
            return d
        with patch.object(gt.time, 'time', return_value=self.now), patch.object(buyer, 'send', send), \
             patch.object(buyer, 'rpc', return_value=None), patch.object(buyer, 'log'), \
             patch.object(buyer, 'decide', side_effect=AssertionError('no model')):
            out = gt.negotiate(buyer.negotiate, self.md, order, self.offer, q, (.9,), self.shop,
                'G12345678', 'fixture', 'fixture goods', changes.pop('limit', 6000), '',
                resume=('fixture',q['task']), ladder=ladder, take_at=None,
                stop_if=lambda text, turn: stopped, decision_guard=guard, **changes)
        return sent, out
    def test_inactive_B_and_later_turn_preserve_treatment_reason(self):
        self.assign()
        with patch.object(gt.time, 'time', return_value=self.cfg['end']):
            sent = []
            gt.negotiate(lambda *a, **kw: sent.append('baseline') or {}, self.md,
                self.order, self.offer, self.q, (.9,), self.shop, 'G12345678',
                'fixture', 'goods', 6000, '', resume=('fixture', self.q['task']))
        self.assertEqual(sent, ['baseline'])
        self.assertEqual(self.order[gt.KEY]['fallback'], 'window_inactive')
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        self.order.pop(gt.KEY)
        self.assign()
        def two_turns(*a, **kw):
            first = kw['ladder'](4000, self.text, [])
            kw['decision_guard'](first, [])
            kw['ladder'](4100, self.text, [('you offered', 4000)])
            return {'history': [('you offered',4000)], 'state':'TASK_STATE_COMPLETED'}
        with patch.object(gt.time,'time',return_value=self.now):
            gt.negotiate(two_turns, self.md, self.order, self.offer, self.q, (.9,),
                self.shop, 'G12345678', 'fixture', 'goods', 6000, '',
                resume=('fixture',self.q['task']), ladder=lambda *a:
                    dict(shop_price=4000,action='offer',price=3600))
        self.assertTrue(self.order[gt.KEY]['applied'])
        self.assertIsNone(self.order[gt.KEY]['fallback'])

    def test_busy_journal_is_nonblocking_and_never_treats(self):
        import fcntl
        import concurrent.futures
        import time
        original = fcntl.flock
        def nonblocking_only(fd, flags):
            self.assertTrue(flags & fcntl.LOCK_NB, 'journal must not wait on another buyer')
            return original(fd, flags)
        with patch.object(gt.fcntl,'flock',nonblocking_only):
            self.assertIsNotNone(self.assign())
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        with open(str(Path(self.md)/gt.JOURNAL)+'.lock','a') as held:
            original(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started=time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as workers:
                orders=[dict(self.order, order='busy'+str(i)) for i in range(8)]
                results=list(workers.map(lambda o: gt.assign(self.md,o,now=self.now,env={}),orders))
            self.assertEqual(results,[None]*8)
            self.assertLess(time.monotonic()-started, 1.0)
            self.assertTrue(all(o[gt.KEY]['fallback']=='journal_failure' for o in orders))

    def test_main_no_stock_retains_assignment_in_order_row(self):
        import market_buyer as mb
        import contextlib
        import io
        p=Path(self.md)
        (p/'shops.json').write_text('[]')
        (p/'catalog.json').write_text('{}')
        (p/'order.json').write_text(json.dumps(self.order | {'acceptable':['G12345678'],
            '_goods_take30_witness':{'input_eligible':True,'raw':'UNTRUSTED fixture'}}))
        argv=['market_buyer.py','--shops',str(p/'shops.json'),'--item','G12345678',
              '--budget','6000','--order',str(p/'order.json')]
        with patch('sys.argv',argv), patch.object(mb.time,'time',return_value=self.now), \
             patch.object(mb,'warm_ledger'), patch.object(mb,'MEMORY',str(p/'memory.json')), \
             patch.object(mb,'AGENT_DIR',self.md), patch.object(mb,'SAVED_SHARE',mb.SAVED_MIN_SHARE), \
             patch.dict(os.environ,{'BUYER_GOODS_TAKE30':'1'}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(mb.main(),0)
        row=json.loads((p/'measure/orders.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['state'],'NO_SHOP_IN_STOCK')
        self.assertEqual(row['goods_take30']['arm'],'B')
        self.assertFalse(row['goods_take30']['applied'])
        self.assertNotIn('goods_take30_witness',row)

    def test_treated_prices_do_not_train_either_arm(self):
        import market_buyer as mb
        control={'sku':'G12345678','item':'G12345678','price':3600,'asks':[4000],
                 'quotes':[{'sku':'G12345678','ask':4000}]}
        treated=dict(control, goods_take30={'arm':'B','applied':True}, price=4000, asks=[4100])
        untreated=dict(control, goods_take30={'arm':'B','applied':False})
        self.assertEqual(mb.priced([control,treated,untreated]),[control,untreated])
        with patch.object(mb,'recent_orders',return_value=[treated]):
            self.assertEqual(mb.recent_asks(self.md,self.now)['G12345678'],[4000])

    def test_real_quote_fulltask_real_main_ladder_and_final_wire_warranty(self):
        import ast
        import market_buyer as mb
        from types import SimpleNamespace
        tree=ast.parse(Path(mb.__file__).read_text())
        ladder=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='price_ladder')
        call=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call)
                  and isinstance(n.value.func,ast.Name) and n.value.func.id=='negotiate')
        for warranty, sw, expected_reason, expected_wire in (
                ('Includes a 6-hour warranty.',None,None,['4000']),
                ('Includes a 72-hour warranty.',None,None,['4000']),
                ('Includes a 3-hour warranty.',{'S':.32,'hist':{}},None,['4000']),
                ('Includes a 3-hour warranty.',None,'no_resume',[]),
                ('No warranty.',{'S':.32,'hist':{}},'no_resume',[]),
                ('',None,'sku_or_warranty',['3600'])):
            with self.subTest(warranty=warranty,sw=sw):
                for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
                order={k:v for k,v in self.order.items() if k != gt.KEY}
                if sw is not None: order['_sw']=sw
                text='fixture goods (G12345678). Asking 4000. '+warranty
                # Long tail is present in q.task, while quote() keeps only 300 chars.
                text=text+' '+('description '*30)
                shop=dict(self.shop,url='fixture',_offer=self.offer)
                got={}
                with patch.object(mb,'serves_acp',return_value=True), \
                     patch.object(mb.httpx,'get',return_value=SimpleNamespace(json=lambda:
                        {'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'fixture'}]})), \
                     patch.object(mb,'buyer_send',return_value=task(text,4000)), patch.object(mb,'log'):
                    mb.quote(shop,'fixture goods','G12345678',order,got)
                q=got[shop['name']]
                self.assertEqual(q['text'],text[:300])
                self.assertEqual(buyer.message_text(q['task']),text)
                self.assertEqual(gt.reason(order,self.offer,q,(.9,),shop,'G12345678',6000,
                    ('fixture',q['task']),None),expected_reason)
                gt.assign(self.md,order,now=self.now,env={})
                env=dict(mb.__dict__,md=self.md,order=order,offer=self.offer,q=q,steps=(.9,),
                    shop=shop,key='G12345678',label='fixture goods',limit=6000,top=6000,note='',r3=True,
                    resume=('fixture',q['task']),probe_arm=None,discount_arm=None,goods_first_arm=None,
                    args=SimpleNamespace(budget=6000))
                # This is the unchanged real nested main ladder, not a fake pricing function.
                exec(compile(ast.fix_missing_locations(ast.Module([ladder],type_ignores=[])),
                     '<real-main-ladder>','exec'),env)
                with patch.object(gt.time,'time',return_value=self.now), patch.object(buyer,'send',
                     return_value=task('',state='TASK_STATE_COMPLETED')) as send, \
                     patch.object(buyer,'rpc',return_value=None), patch.object(buyer,'log'), \
                     patch.object(buyer,'decide',side_effect=AssertionError('no model')):
                    exec(compile(ast.fix_missing_locations(ast.Module([call],type_ignores=[])),
                         '<real-main-negotiate>','exec'),env)
                self.assertEqual([c.args[1] for c in send.call_args_list],expected_wire)
                self.assertEqual(order[gt.KEY]['applied'],expected_reason is None)

    def test_matching_runtime_text_reaches_warranty_guard_not_prefix_guard(self):
        for text, sw, h in (
                ('G12345678 no warranty',None,None),
                ('G12345678 includes a 3-hour warranty',None,3.0),
                ('G12345678 includes a 3-hour warranty',{'S':.32},2.0),
                ('G12345678 includes a 0-hour warranty',{'S':.32},0.0)):
            with self.subTest(text=text,sw=sw,h=h):
                order=dict(self.order)
                if sw is not None: order['_sw']=sw
                q=dict(self.q,text=text,task=task(text,4000),warranty_h=h)
                self.assertEqual(gt.reason(order,self.offer,q,(.9,),self.shop,'G12345678',
                    6000,('fixture',q['task']),None),'sku_or_warranty')

    def test_OFF_witness_is_exact_runtime_hash_only_and_never_drives_policy(self):
        import copy
        import hashlib
        self.flag(enabled=False)
        order=copy.deepcopy(self.order)
        q=copy.deepcopy(self.q)
        before=copy.deepcopy(q)
        baseline={'state':'TASK_STATE_COMPLETED','history':[('you offered',3600)]}
        with patch.object(gt,'config',side_effect=AssertionError('no measurement IO')), \
             patch.object(gt,'_journal',side_effect=AssertionError('no measurement journal')):
            out=gt.negotiate(lambda *a,**kw: baseline,self.md,order,self.offer,q,(.9,),
                self.shop,'G12345678','fixture','goods',6000,'',resume=('fixture',q['task']))
        self.assertIs(out,baseline)
        w=order['_goods_take30_witness']
        self.assertEqual(w['scope'],'selected_pre_negotiate')
        self.assertEqual(w['task_sha256'],hashlib.sha256(q['task']['id'].encode()).hexdigest())
        self.assertEqual(w['fulltext_sha256'],hashlib.sha256(self.text.encode()).hexdigest())
        self.assertEqual(w['fulltext_len'],len(self.text))
        self.assertEqual((w['ask'],w['raw_ask_type'],w['list_price'],w['limit']),(4000,'int',10000,6000))
        self.assertTrue(w['input_eligible'])
        self.assertIsNone(w['reason'])
        self.assertEqual(w['window_status'],'unassigned')
        self.assertIsNone(w['treatment_eligible'])
        self.assertFalse(w['switch_checked'])
        self.assertNotIn(gt.KEY,order)
        self.assertEqual(q,before)
        self.assertNotIn(self.text,json.dumps(w))
        self.assertNotIn('"task"',json.dumps(w))
        import market_buyer as mb
        row=mb.quote_row('fixture-shop',self.offer,q,6000,'fixture-shop')
        self.assertNotIn('goods_take30_witness',row)

    def test_witness_uses_identical_guard_and_recorded_window_not_live_switch(self):
        import copy
        self.assign()
        for text, sw, h, expected in (
                (self.text,None,None,None),
                ('G12345678 3-hour warranty',{'S':.32},3.0,None),
                ('G12345678 3-hour warranty',{'S':.32},2.0,'sku_or_warranty'),
                ('G12345678 no warranty',{'S':.32},0.0,'sku_or_warranty')):
            order=copy.deepcopy(self.order)
            if sw is not None: order['_sw']=sw
            q=dict(self.q,text=text,warranty_h=h,task=task(text,4000))
            for now,status in ((self.cfg['start']-1,'before'),(self.now,'within_recorded_window'),
                               (self.cfg['end'],'expired')):
                w=gt.witness(order,self.offer,q,(.9,),self.shop,'G12345678',6000,
                             ('fixture',q['task']),None,now=now)
                self.assertEqual((w['reason'],w['input_eligible']),(expected,expected is None))
                self.assertEqual(w['window_status'],status)
                self.assertFalse(w['switch_checked'])
                self.assertIsNone(w['treatment_eligible'])
        overlap=dict(self.order,_our_ask={'arm':'B'})
        w=gt.witness(overlap,self.offer,self.q,(.9,),self.shop,'G12345678',6000,
                     ('fixture',self.q['task']),None)
        self.assertEqual(w['overlap_reason'],'overlapping_trial')
        for raw in (True,4000.0,'4000',None):
            q=copy.deepcopy(self.q)
            if raw is None: q['task']['status']['message'].pop('metadata')
            else: q['task']['status']['message']['metadata']['ask']=raw
            w=gt.witness(self.order,self.offer,q,(.9,),self.shop,'G12345678',6000,
                         ('fixture',q['task']),None)
            self.assertFalse(w['input_eligible'])
            self.assertEqual(w['raw_ask_type'],type(raw).__name__)

    def test_witness_failure_is_private_and_food_never_computes_it(self):
        baseline={'state':'UNKNOWN_RPC_OUTCOME','history':[('you offered',3600)]}
        with patch.object(gt.buyer,'message_text',side_effect=ValueError('PRIVATE fixture')):
            out=gt.negotiate(lambda *a,**kw:baseline,self.md,self.order,self.offer,self.q,(.9,),
                self.shop,'G12345678','fixture','goods',6000,'',resume=('fixture',self.q['task']))
        self.assertIs(out,baseline)
        self.assertEqual(self.order['_goods_take30_witness']['reason'],'witness_error')
        self.assertIsNone(self.order['_goods_take30_witness']['input_eligible'])
        self.assertNotIn('PRIVATE',json.dumps(self.order['_goods_take30_witness']))
        food=dict(kind='food')
        with patch.object(gt,'witness',side_effect=AssertionError('food not measured')):
            self.assertIs(gt.negotiate(lambda *a,**kw:baseline,self.md,food,self.offer,self.q,
                (.9,),self.shop,'G12345678','fixture','goods',6000,''),baseline)
        self.assertNotIn('_goods_take30_witness',food)

    def test_order_record_reuses_selected_witness_without_reparse_or_learning_change(self):
        import market_buyer as mb
        from types import SimpleNamespace
        gt.negotiate(lambda *a,**kw:{},self.md,self.order,self.offer,self.q,(.9,),self.shop,
                     'G12345678','fixture','goods',6000,'',resume=('fixture',self.q['task']))
        shop=dict(self.shop,_offer=self.offer,usable=[self.offer],_b=0)
        other=dict(name='other',_offer=self.offer,usable=[self.offer],_b=0)
        def record(order):
            return mb.order_record(order,SimpleNamespace(budget=6000,item='goods'),
                [shop,other],{shop['name']:self.q,'other':self.q},shop['name'],'choice',
                shop,'chosen',self.offer,6000,0,{'state':'TASK_STATE_COMPLETED','history':[]},
                None,{'receipt':None},[],[],False,None,{}, {})
        with patch.object(mb,'buyer_cfg',return_value={}), patch.object(gt,'witness',
                side_effect=AssertionError('snapshot once only')):
            measured=record(self.order)
            baseline=record({k:v for k,v in self.order.items() if k!='_goods_take30_witness'})
        w=self.order['_goods_take30_witness']
        compact=measured['goods_take30_witness']
        self.assertIsInstance(compact,list)
        self.assertEqual(compact[0],2)
        self.assertEqual(compact[1:4],[w['task_sha256'],w['fulltext_sha256'],w['fulltext_len']])
        self.assertEqual([len(compact[1]),len(compact[2])],[64,64])
        self.assertTrue(all('goods_take30_witness' not in q for q in measured['quotes']))
        stripped=dict(measured)
        stripped.pop('goods_take30_witness')
        stripped['quotes']=[{k:v for k,v in q.items() if k!='goods_take30_witness'} for q in measured['quotes']]
        self.assertEqual(stripped,baseline)
        # Witness-only rows are baseline prices, not treatments.
        with patch.object(mb,'recent_orders',return_value=[measured]):
            asks=mb.recent_asks(self.md,self.now)
        with patch.object(mb,'recent_orders',return_value=[baseline]):
            self.assertEqual(mb.recent_asks(self.md,self.now),asks)
        self.assertEqual(mb.priced([measured]),[measured])
        with patch.object(mb.records.os,'write',wraps=mb.records.os.write) as write:
            mb.records.write(self.md,'orders',measured)
            self.assertEqual(write.call_count,1)
            written=write.call_args.args[1]
            self.assertEqual(json.loads(written)['goods_take30_witness'],compact)
            self.assertEqual(written.count(b'goods_take30_witness'),1)
        # Actual whole-row serializer cost, not just the in-memory witness object.
        encoded=lambda r: (json.dumps(r,ensure_ascii=False)+'\n').encode()
        added=len(encoded(measured))-len(encoded(baseline))
        self.assertGreater(added,150)  # two complete hashes remain, never 16hex truncations
        self.assertLessEqual(added,300)
        self.assertEqual(len(json.dumps(compact)),len(json.dumps(json.loads(json.dumps(compact)))))

    def test_physical_byte_tail_coverage_is_measured_not_claimed_identical(self):
        import buyer_rank_pg as rp
        import buyer_shop_profile as sp
        import market_buyer as mb
        note=gt.witness(self.order,self.offer,self.q,(.9,),self.shop,'G12345678',6000,
                        ('fixture',self.q['task']),None,now=self.now)
        compact=gt.compact_witness(note)
        sets={}
        for mode in ('baseline','compact','duplicated'):
            path=Path(self.md)/mode/'measure/orders.jsonl'
            path.parent.mkdir(parents=True)
            data=[]
            for i in range(60):
                r=dict(order='o'+str(i),ts=self.now-60*(60-i),kind='goods',shop='fixture-shop',
                       sku='G12345678',list_price=10000,limit=6000,quotes=[dict(shop='fixture-shop',
                       sku='G12345678',ask=4000,text='fixture '*12)])
                if mode=='compact': r['goods_take30_witness']=compact
                if mode=='duplicated':
                    r['goods_take30_witness']=note
                    r['quotes'][0]['goods_take30_witness']=note
                data.append((json.dumps(r,ensure_ascii=False)+'\n').encode())
            raw=b''.join(data);path.write_bytes(raw)
            budget=3000  # scaled byte-boundary fixture; production caps are never changed
            expected=[json.loads(x)['order'] for x in raw[-budget:].splitlines()[1:]]
            rows=list(rp._tail(str(path),nbytes=budget))
            self.assertEqual([r['order'] for r in rows],expected)
            self.assertEqual([r['order'] for r in sp._tail(str(path),nbytes=budget)],expected)
            with patch.object(mb,'RECENT_FIT_BYTES',budget):
                self.assertEqual([r['order'] for r in mb.recent_orders(str(path.parent.parent),self.now,3)],expected)
            sets[mode]=expected
        self.assertLess(len(sets['compact']),len(sets['baseline']))
        self.assertGreater(len(sets['compact']),len(sets['duplicated']))
        # Every bounded reader keeps the newest records; missing prefix is not zero activity.
        self.assertEqual(sets['baseline'][-len(sets['compact']):],sets['compact'])
        self.assertEqual(rp.TAIL_BYTES,30_000_000)
        self.assertEqual(sp.TAIL_BYTES,30_000_000)
        self.assertEqual(mb.RECENT_FIT_BYTES,3_000_000)

    def test_compact_witness_preserves_full_hashes_guards_and_failure_unknown(self):
        base=gt.witness(self.order,self.offer,self.q,(.9,),self.shop,'G12345678',6000,
                        ('fixture',self.q['task']),None,now=self.now)
        for raw_type in ('NoneType','bool','int','float','str','list','dict','other'):
            for why in (None,'invalid_runtime_shape','sku_or_warranty','witness_error'):
                n=dict(base,raw_ask_type=raw_type,reason=why,warranty_h=999999999999.9,
                       warranty_normal=True,warranty_short=True,short_parser_match=True,quote_closed=True)
                c=gt.compact_witness(n)
                self.assertEqual(c[1:4],[base['task_sha256'],base['fulltext_sha256'],base['fulltext_len']])
                self.assertEqual(c[5],15)
                self.assertEqual(c[7],why)
                self.assertLessEqual(len(json.dumps({'goods_take30_witness':c}).encode()),300)
        error=gt.compact_witness({'reason':'witness_error'})
        self.assertEqual(error[7],'witness_error')
        self.assertIsNone(error[1])

    def test_B_exact_ask_reaches_real_send(self):
        sent, out = self.run_wire()
        self.assertEqual(sent, ['4000'])
        self.assertTrue(self.order['_goods_take30']['applied'])
        self.assertEqual(out['history'], [('shop asked',4000),('you offered',4000)])
    def test_expiry_at_final_guard_is_baseline_not_B(self):
        self.assertEqual(self.run_wire(expire=True)[0], ['3600'])
    def test_stop_if_has_precedence(self):
        self.assertEqual(self.run_wire(stopped='no warranty')[0], [])
    def test_unknown_is_never_retried(self):
        sent, out = self.run_wire(unknown=True)
        self.assertEqual(sent, ['4000'])
        self.assertEqual(out['state'], 'UNKNOWN_RPC_OUTCOME')
    def test_repeated_same_ask_not_retaken(self):
        self.assertEqual(self.run_wire(repeat=True)[0], ['4000'])
    def test_metadata_conflict_or_no_warranty_or_no_resume_is_A(self):
        for q in (dict(self.q, ask=4001), dict(self.q, ask_src='text'),
                  dict(self.q, text='G12345678 no warranty'), dict(self.q, closed=True),
                  dict(self.q, task=task(self.text, 4500))):
            with self.subTest(q=q):
                # Different invocation requires a fresh journal for each case.
                self.order.pop('_goods_take30',None)
                for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
                self.assertEqual(self.run_wire(q=q)[0], ['3600'])
    def test_invalid_windows_and_money_fail_closed(self):
        for changes in (dict(start=None),dict(start=True),dict(end=float('nan')),
                        dict(end=float('inf')),dict(end=2000001801),dict(enabled=False)):
            self.flag(**changes)
            self.assertIsNone(gt.config(self.md, {}, self.now))
        self.flag()
        for now in (1999999999,2000001800,True,float('nan'),float('inf')):
            self.assertIsNone(gt.config(self.md, {}, now))
    def test_reused_order_and_journal_failure_fail_closed(self):
        self.assertIsNotNone(self.assign())
        other=dict(self.order)
        self.assertIsNone(gt.assign(self.md, other, now=self.now, env={}))
        self.assertEqual(other['_goods_take30']['fallback'], 'reused_order')
        self.order.pop('_goods_take30',None)
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        with patch.object(gt.os,'fsync',side_effect=OSError('fixture')):
            self.assertIsNone(self.assign())
        self.assertFalse(self.order['_goods_take30']['applied'])

    def test_A_OFF_end_and_positive_short_warranty(self):
        self.order['order']=next('a'+str(i) for i in range(100) if gt.arm(self.cfg,'a'+str(i))=='A')
        self.assertEqual(self.run_wire()[0], ['3600'])
        for p in Path(self.md).glob(gt.JOURNAL+'*'): p.unlink()
        self.flag(enabled=False)
        self.assertEqual(self.run_wire()[0], ['3600'])
        self.flag()
        self.order['order']=next('b'+str(i) for i in range(100) if gt.arm(self.cfg,'b'+str(i))=='B')
        self.order['_sw']={'enabled':True}
        self.text=self.text.replace('6-hour','3-hour')
        self.q.update(text=self.text,warranty_h=3.0,task=task(self.text,4000))
        self.assertEqual(self.run_wire()[0], ['4000'])

    def test_negative_bool_zero_overcap_noresume_and_task_or_sku_race(self):
        for a in (-1,True,0,5001,6001,float('nan'),float('inf'),4000.1):
            with self.subTest(ask=a):
                q=dict(self.q,ask=a)
                # Direct scope check cannot accidentally enter the bare TAKE branch.
                self.assertIsNotNone(gt.reason(self.order,self.offer,q,(.9,),self.shop,'G12345678',
                    6000,('fixture',q['task']),None))
        self.assertIsNotNone(gt.reason(self.order,self.offer,self.q,(.9,),self.shop,'G12345678',0,None,None))
        self.assertIsNotNone(gt.reason(self.order,self.offer,self.q,(.9,),self.shop,'G87654321',6000,
            ('fixture',self.q['task']),None))

    def test_intent_io_failure_and_mutated_allocation_are_A(self):
        self.assign()
        original=gt._journal
        def journal(md,cfg,event,oid,note=None):
            if event=='intent': raise OSError('fixture intent failure')
            return original(md,cfg,event,oid,note)
        with patch.object(gt,'_journal',journal):
            self.assertEqual(self.run_wire()[0], ['3600'])
        self.assertFalse(self.order['_goods_take30']['applied'])

    def test_real_main_negotiate_expression_reaches_wire_and_food_unchanged(self):
        import ast
        import market_buyer as mb
        from types import SimpleNamespace
        tree=ast.parse(Path(mb.__file__).read_text())
        assignment=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
            isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='negotiate')
        self.assign()
        env=dict(mb.__dict__,md=self.md,order=self.order,offer=self.offer,q=self.q,steps=(.9,),
            shop=dict(name='fixture-shop',url='fixture'),key='G12345678',label='fixture goods',limit=6000,
            top=6000,note='',r3=True,resume=('fixture',self.q['task']),
            probe_arm=None,discount_arm=None,price_ladder=lambda m,t,h:
                dict(shop_price=m,action='offer',price=3600),
            request_text=lambda *args:'fixture request',quote_refused=lambda *args,**kw:None,
            goods_text_refused=lambda *args:None,shop_question_reply=lambda *args:None)
        with patch.object(gt.time,'time',return_value=self.now), patch.object(buyer,'send',
             return_value=task('',state='TASK_STATE_COMPLETED')) as send, patch.object(buyer,'log'):
            exec(compile(ast.fix_missing_locations(ast.Module([assignment],type_ignores=[])),
                 '<real-main-negotiate>', 'exec'),env)
            self.assertEqual(send.call_args.args[1], '4000')
        # Same actual main expression with no policy assignment: baseline wire + measurement only.
        env['order']={k:v for k,v in self.order.items() if k not in (gt.KEY,'_goods_take30_witness')}
        with patch.object(buyer,'send',return_value=task('',state='TASK_STATE_COMPLETED')) as send, \
             patch.object(buyer,'log'), patch.object(gt,'config',side_effect=AssertionError('OFF reader unused')):
            exec(compile(ast.fix_missing_locations(ast.Module([assignment],type_ignores=[])),
                 '<real-main-OFF-negotiate>', 'exec'),env)
            self.assertEqual(send.call_args.args[1],'3600')
        self.assertTrue(env['order']['_goods_take30_witness']['input_eligible'])
        self.assertNotIn(gt.KEY,env['order'])
        env['order']={'kind':'food'}
        def unchanged(*args,**kw):
            self.assertEqual(kw['take_at'],6000)
            return {'state':'food-unmodified'}
        env.update(negotiate=unchanged,FOOD_TAKE_ASK=True,FOOD_TAKE_SHARE=.65)
        exec(compile(ast.fix_missing_locations(ast.Module([assignment],type_ignores=[])),
             '<real-main-food>', 'exec'),env)
        self.assertEqual(env['out']['state'],'food-unmodified')

if __name__ == '__main__': unittest.main()
