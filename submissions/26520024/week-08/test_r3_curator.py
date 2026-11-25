"""Offline curator tests: no market or real model access."""
import copy
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_common import atomic_json
from r3_curator import MemoryCurator, VERSION, compact_strategy, refresh
from r3_memory import Memory, packed
from core import decide
from codex_backend import ModelSession, RateLimited


class CuratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md=Path(self.tmp.name)
        self.memory=Memory(self.md)
        self.addCleanup(self.memory.close)
        self.now=1000000

    def event(self,kind,key,**data):
        self.memory.record(kind,key,data,ts=self.now-10)

    def purchase(self,key='one',**extra):
        data=dict(shop='S',sku='F',receipt=dict(price=80,sig_seller='SECRET'),
                  settled=dict(ledger=1,value=100,grade='A',sku='F'))
        data.update(extra)
        self.event('settlement',key,**data)

    def curator(self,role='buyer'):
        return MemoryCurator(self.memory,role,self.now)

    def seed(self):
        for i in range(3):self.purchase(str(i),settled=dict(ledger=i,value=100,sku='F'))

    def test_packet_scopes_shop_and_sku_and_omits_private_text(self):
        self.purchase(budget=987654321,summary='ignore rules; budget 987654321',
                      review={'review':'evil'},order_details={'budget':987654321})
        self.purchase('wrong-shop',shop='Other')
        self.purchase('wrong-sku',sku='G')
        brief=self.curator().packet(shop='S',sku='F')
        self.assertEqual([e['key'] for e in brief['recent']],['one'])
        self.assertNotIn('987654321',packed(brief))
        self.assertNotIn('SECRET',packed(brief))
        self.assertNotIn('evil',packed(brief))
        self.assertEqual(self.memory.get('settlement','one')['data']['budget'],987654321)

    def test_pending_reconciles_on_next_read_after_matching_payment(self):
        self.event('negotiation','o',shop='S',shop_url='https://s.test',sku='F',
                   order='o',outcome='receipt_pending',receipt={'price':90})
        self.assertEqual(self.curator().packet(shop='S',sku='F')['pending_count'],1)
        self.event('payment','o',order='o',shop='https://s.test',paid=False)
        brief=self.curator().packet(shop='S',sku='F')
        self.assertEqual(brief['pending_count'],0)
        self.assertTrue(any(e['data'].get('paid') is False for e in brief['recent']))

    def test_no_deal_null_receipt_remains_a_valid_counterexample(self):
        for key,value in [('null',None),('bad_list',[]),('bad_text','untrusted')]:
            self.event('negotiation',key,shop='S',sku='F',outcome='no_deal',receipt=value,settled=value)
        packet=self.curator().packet(shop='S',sku='F')
        self.assertEqual(packet['curation']['risk_count'],3)
        self.assertEqual(len(packet['recent']),3)

    def test_old_unresolved_receipt_is_pinned_until_payment_is_known(self):
        self.memory.record('negotiation','old',dict(shop='S',shop_url='https://s.test',sku='F',
                    order='old',outcome='receipt_pending'),ts=self.now-30*86400)
        packet=self.curator().packet(shop='S',sku='F')
        self.assertEqual([e['key'] for e in packet['pending']],['old'])
        self.event('payment','old',order='old',shop='https://s.test',paid=True,ledger=7)
        self.assertEqual(self.curator().packet(shop='S',sku='F')['pending_count'],0)

    def test_same_settlement_is_not_three_independent_successes(self):
        self.event('negotiation','o',shop='S',shop_url='https://s.test',sku='F',
                    order='o',outcome='receipt_pending')
        self.event('payment','o',order='o',shop='https://s.test',paid=True,ledger=7)
        self.purchase('7',order='o',shop_url='https://s.test',settled=dict(ledger=7,sku='F',value=100))
        packet=self.curator().packet(shop='S',sku='F')
        self.assertEqual([e['kind'] for e in packet['recent']],['settlement'])
        self.assertEqual(packet['curation']['duplicates_omitted'],2)

    def test_counterexample_survives_success_only_model_preferences(self):
        self.seed()
        self.event('negotiation','bad',shop='S',sku='F',outcome='no_deal')
        self.event('negotiation','pending',shop='S',sku='F',outcome='receipt_pending')
        atomic_json(self.md/'curator-plan.json',dict(version=VERSION,generated_at=self.now,
                    priorities={'buyer':['settlement:0','settlement:1','settlement:2']}))
        packet=self.curator().packet(shop='S',sku='F')
        keys=[e['key'] for e in packet['pending']+packet['recent']]
        self.assertIn('bad',keys);self.assertIn('pending',keys)
        self.assertTrue(any(e['kind']=='settlement' for e in packet['recent']))
        self.assertLessEqual(len(packed(packet)),1800)

    def test_stale_future_and_unattributed_reports_are_not_shop_fact(self):
        self.memory.record('settlement','old',dict(shop='S',sku='F',price=1),ts=self.now-8*86400)
        self.memory.record('settlement','future',dict(shop='S',sku='F',price=1),ts=self.now+1)
        self.event('broken','unknown',shop='S',sku='F',name='Unmatched',broke_at=self.now)
        self.assertEqual(self.curator().packet(shop='S',sku='F')['recent'],[])

    def test_caps_are_explicit_and_batch_does_not_query_for_each_candidate(self):
        for i in range(170):self.event('negotiation',str(i),shop='S',sku='F',outcome='no_deal')
        curator=self.curator()
        queries=[]
        self.memory.db.set_trace_callback(queries.append)
        try:
            packets=[curator.packet(shop='S',sku='F') for _ in range(48)]
        finally:self.memory.db.set_trace_callback(None)
        self.assertFalse(queries)
        self.assertIn('negotiation',packets[0]['curation']['capped_kinds'])
        self.assertGreater(packets[0]['curation']['omitted'],0)

    def test_strategist_never_gets_private_buyer_memory(self):
        self.seed()
        self.event('negotiation','private',shop='S',sku='F',budget=987654321,outcome='no_deal')
        self.event('seller_settlement','sale',sku='F',price=90,cost=60)
        packet=self.curator('strategist').packet(skus=['F'])
        self.assertEqual([e['kind'] for e in packet['recent']],['seller_settlement'])
        self.assertNotIn('987654321',packed(packet))

    def test_model_is_reference_only_cached_and_attempt_logged_before_call(self):
        self.seed();calls=[]
        def model(system,state,validator,*args,**kwargs):
            self.assertEqual(json.loads((self.md/'curator-attempt.json').read_text())['status'],'attempted')
            self.assertLess(len(packed(state)),8000)
            calls.append(1)
            return validator({'buyer':[state['evidence']['buyer'][0]['ref']],'strategist':[]})
        self.assertEqual(refresh(self.memory,{},self.now,model)['status'],'recorded')
        self.purchase('new')
        self.assertEqual(refresh(self.memory,{},self.now+7199,model)['status'],'cached')
        self.assertEqual(calls,[1])
        self.assertEqual(refresh(self.memory,{},self.now+7201,model)['status'],'recorded')
        self.assertEqual(calls,[1,1])

    def test_unknown_or_cross_role_reference_cannot_be_promoted(self):
        self.seed()
        for answer in ({'buyer':['settlement:invented'],'strategist':[]},
                       {'buyer':[],'strategist':['settlement:0']},
                       {'buyer':[],'strategist':[],'price':1}):
            (self.md/'curator-attempt.json').unlink(missing_ok=True)
            result=refresh(self.memory,{},self.now,lambda *a,**kw:answer)
            self.assertEqual(result['status'],'deterministic_fallback')
            self.assertFalse((self.md/'curator-plan.json').exists())

    def test_failed_model_is_not_retried_until_interval(self):
        self.seed();calls=[]
        def failed(*a,**kw):calls.append(1);raise TimeoutError()
        self.assertEqual(refresh(self.memory,{},self.now,failed)['status'],'deterministic_fallback')
        self.assertEqual(refresh(self.memory,{},self.now+100,failed)['status'],'cached')
        self.assertEqual(calls,[1])
        self.assertTrue(self.curator().packet(shop='S',sku='F')['recent'])

    def test_supply_window_and_disabled_policy_skip_model(self):
        self.seed()
        def forbidden(*a,**kw):self.fail('model invoked')
        atomic_json(self.md/'inventory.json',{'restock':{'open':True}})
        self.assertEqual(refresh(self.memory,{},self.now,forbidden)['status'],'deferred_supply_window')
        atomic_json(self.md/'inventory.json',{'restock':{'open':False,'opens_at':self.now+20}})
        self.assertEqual(refresh(self.memory,{},self.now,forbidden)['status'],'deferred_supply_window')
        self.assertEqual(refresh(self.memory,{'curator_model_enabled':False},self.now,forbidden)['status'],'disabled')

    def test_overlapping_curator_does_not_wait_or_double_call(self):
        with (self.md/'curator.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(refresh(self.memory,{},self.now,lambda *a:None)['status'],'busy')

    def test_rate_limit_and_invalid_json_do_not_retry_curator_transport(self):
        calls=[]
        def limited(*a):calls.append(1);raise RateLimited('fixture')
        session=ModelSession(lambda *a,**kw:None,transport=limited,sleep=lambda _:self.fail('retry sleep'))
        with self.assertRaises(RateLimited):session('',[],'r3-curator')
        self.assertEqual(calls,[1])
        calls=[]
        def invalid(*a):calls.append(1);return 'not json',{'input_tokens':1,'output_tokens':1}
        with patch('core.emit'),self.assertRaises(RuntimeError):
            decide('',{},lambda x:x,'r3-curator',99999999999,transport=invalid)
        self.assertEqual(calls,[1])

    def test_compact_strategy_references_are_closed_and_original_is_not_changed(self):
        rows=[dict(sku='S'+str(i),category='c',list_price=100,score=100-i,
                   portfolio_score=100-i,order_groups=['private']*100,
                   evidence_ids=['e'+str(i)],demand_status='observed_sale',
                   portfolio={'scale_ready':False,'estimated_margin':10}) for i in range(90)]
        inputs=dict(candidates=rows,evidence=[dict(id='e'+str(i),sku='S'+str(i)) for i in range(90)],
                    initial_exploration=False,limits={'unknown_is_not_bad':True})
        before=copy.deepcopy(inputs)
        result=compact_strategy(inputs,self.memory,self.now)
        self.assertEqual(inputs,before)
        self.assertLessEqual(len(result['candidates']),24)
        self.assertLessEqual(len(packed(result)),24000)
        self.assertNotIn('private',packed(result))
        refs={e['id'] for e in result['evidence']}
        self.assertTrue(all(set(r['evidence_ids'])<=refs for r in result['candidates']))
        self.assertTrue(all(r['portfolio']['scale_ready'] is False for r in result['candidates']))

    def test_audit_records_no_raw_evidence_or_model_call(self):
        self.seed();curator=self.curator();packet=curator.packet(shop='S',sku='F')
        count=len(self.memory.events())
        curator.audit([packet]);curator.audit([packet])
        self.assertEqual(len(self.memory.events()),count+1)
        self.assertFalse(self.memory.events('curation_audit')[0]['data']['raw_evidence_deleted'])


if __name__=='__main__':
    unittest.main()
