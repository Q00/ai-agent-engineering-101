"""Offline protocol and outcome tests: no actual market trades or model calls."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import decision_audit as audit
import buyer_memory
import buyer_r3
import market_buyer_r3 as buyer
import market_memory
import review_r3
import seller_r3
from ops.decision_report import report
import test_seller_persistence as fixtures


class CorrelationTests(unittest.TestCase):
    def test_concurrent_tasks_and_threaded_model_logs_are_isolated(self):
        async def check(name):
            rows=[]
            def write(event,**fields):rows.append({'event':event,**fields})
            with audit.scope('seller',write,episode_id=f'seller:{name}',task_id=name):
                decision=audit.begin('opening')
                await asyncio.sleep(0)
                threaded=await asyncio.to_thread(audit.links)
                audit.proposed({'action':'ask','price':10})
                audit.guard('test_bound')
                audit.executed('ask',price=11)
                self.assertEqual(threaded['decision_id'],decision)
                self.assertTrue(all(r['task_id']==name and r['decision_id']==decision for r in rows))
                self.assertNotIn('decision_id',audit.enrich({'task_id':'another'}))
            self.assertEqual(audit.links(),{})
            return decision
        async def run():return await asyncio.gather(check('a'),check('b'))
        self.assertEqual(len(set(asyncio.run(run()))),2)

    def test_ambiguous_buyer_payment_is_not_assigned_to_last_attempt(self):
        row={'event':'negotiation','order':'o','shop':'url','sku':'s','receipt_price':10,
             'result':'receipt_pending','attempt_id':'a','task_id':'t1','episode_id':'buyer:o','decision_id':'d1'}
        history=[row,{**row,'attempt_id':'b','task_id':'t2','decision_id':'d2'}]
        self.assertEqual(audit.buyer_outcome_links(history,order='o',shop='url',sku='s',price=10),
                         {'outcome_link_status':'ambiguous'})
        linked=audit.buyer_outcome_links(history,order='o',shop='url',sku='s',price=10,task_id='t1')
        self.assertEqual(linked['attempt_id'],'a')
        self.assertEqual(audit.buyer_outcome_links(history,order='o',shop='url',sku='s'),
                         {'outcome_link_status':'unmatched'})
        with audit.scope('buyer',lambda *a,**k:None,episode_id='buyer:other',decision_id='unrelated'):
            self.assertNotIn('decision_id',audit.enrich({'outcome_link_status':'ambiguous'}))

    def test_name_only_breakage_cannot_inherit_current_purchase_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);broken=root/'broken.jsonl';events=root/'events.jsonl'
            broken.write_text(json.dumps({'name':'Food','broke_at':1000})+'\n')
            with audit.scope('buyer',buyer_memory.record,order='current',episode_id='buyer:current'):
                audit.begin('selection')
                buyer_memory.import_broken(broken,event_path=events)
            row=buyer_memory.read_events(events)[0]
            self.assertNotIn('episode_id',row);self.assertNotIn('order',row)
            self.assertNotIn('decision_id',row)


class SellerAuditTests(unittest.TestCase):
    setUp=fixtures.PersistenceTests.setUp
    tearDown=fixtures.PersistenceTests.tearDown
    write_inventory=fixtures.PersistenceTests.write_inventory
    send=fixtures.PersistenceTests.send
    client=fixtures.PersistenceTests.client

    def test_guarded_quote_restart_receipt_and_payment_form_one_episode(self):
        self.unit.update(kind='goods',expires_at=None,grade='A');self.write_inventory()
        answers=[{'action':'ask','price':100,'unit_id':'invalid','reason':'test quote'},
                 {'action':'accept','price':None,'unit_id':'u1','reason':'test low acceptance'},
                 {'action':'accept','price':None,'unit_id':'u1','reason':'test agreement'}]
        async def flow():
            async with self.client() as client:
                first=await self.send(client,'I want F1')
            async with self.client() as client:
                counter=await self.send(client,'100',first['id'])
                self.assertEqual(counter['status']['state'],'TASK_STATE_INPUT_REQUIRED')
                last=await self.send(client,'800',first['id'])
                self.assertEqual(last['status']['state'],'TASK_STATE_COMPLETED')
            return first['id']
        with patch.object(seller_r3,'decide_json',side_effect=answers):task=asyncio.run(flow())
        before=market_memory.events()
        decisions=[r for r in before if r['event']=='decision_proposed']
        executions=[r for r in before if r['event']=='decision_executed']
        self.assertEqual(len({r['decision_id'] for r in decisions}),3)
        self.assertEqual([r['executed']['action'] for r in executions],['ask','counter','accept'])
        self.assertTrue(all(r['execution_status']=='emitted' for r in executions))
        self.assertEqual(executions[1]['parent_decision_id'],executions[0]['decision_id'])
        self.assertEqual(executions[2]['parent_decision_id'],executions[1]['decision_id'])
        self.assertEqual(executions[0]['proposed']['price'],100)
        self.assertGreater(executions[0]['executed']['price'],500)
        self.assertEqual({g['reason'] for g in executions[0]['guards']},
                         {'unavailable_model_unit','warranty_price_floor'})
        self.assertEqual(executions[1]['guards'][0]['reason'],'warranty_price_floor')
        self.assertEqual(report(Path.cwd(),'seller')['episodes'][0]['status'],'receipt_pending')
        kit=Path('logs/kit-fixture.log')
        kit.write_text('[acp] settled Example at 800, ledger #77, unit u1 grade A\n')
        self.assertEqual(market_memory.sync_settlements(kit,cursor_path=Path('state/cursor-test.json')),1)
        self.assertEqual(market_memory.sync_settlements(kit,cursor_path=Path('state/cursor-test.json')),0)
        journey=report(Path.cwd(),'seller')['episodes'][0]
        self.assertEqual(journey['episode_id'],f'seller:{task}')
        self.assertEqual(journey['status'],'paid');self.assertEqual(journey['ledgers'],[77])
        self.assertEqual(len(journey['decisions']),3)
        paid=[r for r in journey['outcomes'] if r['event']=='settled'][0]
        self.assertEqual(paid['decision_id'],executions[-1]['decision_id'])

    def test_ambiguous_receipts_and_unrelated_current_request_are_not_payment_links(self):
        for task in ('a','b'):
            market_memory.record('receipt_signed',task_id=task,unit='u1',price=800,sku='F1',
                                 decision_id=task,episode_id=f'seller:{task}')
        kit=Path('logs/kit-fixture.log');kit.write_text('[acp] settled Example at 800, ledger #7, unit u1 grade A\n')
        with audit.scope('seller',market_memory.record,episode_id='seller:unrelated',task_id='unrelated'):
            audit.begin('opening')
            market_memory.sync_settlements(kit,cursor_path=Path('state/cursor-test.json'))
        paid=[r for r in market_memory.events() if r['event']=='settled'][0]
        self.assertIsNone(paid['task_id']);self.assertNotIn('decision_id',paid)
        self.assertNotIn('episode_id',paid);self.assertEqual(paid['outcome_link_status'],'ambiguous')

    def test_expired_reservation_is_unknown_not_a_failed_or_paid_trade(self):
        async def flow():
            app_seller=seller_r3.Seller()
            # Exercise the same persisted deal fields that survive a restart.
            with audit.scope('seller',market_memory.record,episode_id='seller:t1',task_id='t1'):
                decision=audit.begin('opening');audit.proposed({'action':'ask','price':800})
                audit.executed('ask',status='emitted',price=800)
            app_seller.open['t1']={'unit':self.unit,'last_activity':0,'opened_monotonic':0,
                                   'last_decision_id':decision}
            app_seller.expire_negotiations(now=301)
        asyncio.run(flow())
        journey=report(Path.cwd(),'seller')['episodes'][0]
        self.assertEqual(journey['status'],'expired_unknown')
        self.assertEqual(journey['outcomes'][0]['decision_id'],journey['decisions'][0]['decision_id'])

    def test_promotion_records_model_base_and_actual_quote_separately(self):
        Path('state').mkdir(exist_ok=True)
        Path('state/seller-opening-event.json').write_text(json.dumps({'id':'fixture',
             'starts_at':time.time()-100,'ends_at':time.time()+3600,
             'discount_starts_at':time.time()-1,'discount_bps':300}))
        async def flow():
            async with self.client() as client:await self.send(client,'I want F1')
        with patch.object(seller_r3,'decide_json',return_value={'action':'ask','price':1000,'unit_id':'u1','reason':'base quote'}):
            asyncio.run(flow())
        row=next(r for r in market_memory.events() if r['event']=='decision_executed')
        self.assertEqual(row['proposed']['price'],1000)
        self.assertEqual(row['executed']['price'],970)
        self.assertEqual(row['guards'][0]['reason'],'opening_promotion')


class BuyerAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.before=Path.cwd();os.chdir(self.tmp.name)
        self.addCleanup(self.tmp.cleanup);self.addCleanup(os.chdir,self.before)
        self.order={'order':'o1','kind':'food','item':'food','acceptable':['F1'],'budget':1200,
                    'use_within_hours':4,'expires':time.time()+500}
        self.catalog={'F1':{'kind':'food','list_price':1000,'shelf_hours':100,'name':'Food'}}
        self.shops=[{'name':'shop','url':'https://shop','offers':[{'sku':'F1','qty':1}]}]
        self.answer={'candidate_id':'0','opening_offer':850,'reason':'test hypothesis'}

    def test_model_selection_clamp_actual_send_and_authoritative_payment(self):
        receipt={'item':'F1','price':650,'task':'t1','sig_seller':'fixture'}
        responses=[{'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED',
                    'message':{'parts':[{'text':'Asking 650'}]}}},
                   {'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},
                    'artifacts':[{'parts':[{'data':receipt}]}]}]
        with patch.object(buyer,'decide_json',return_value=self.answer), \
             patch.object(buyer_r3,'card_endpoint',return_value='https://shop/rpc'), \
             patch.object(buyer_r3,'send',side_effect=responses) as send, \
             patch.object(buyer.buyer_forum_memory,'ingest'), \
             patch.object(buyer.buyer_forum_memory,'retrieve',return_value={}):
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0)
        self.assertEqual(result['receipt']['price'],650)
        self.assertEqual(send.call_args_list[-1].args[2],'650')
        rows=buyer_memory.read_events()
        proposal=next(r for r in rows if r['event']=='decision_proposed')
        executions=[r for r in rows if r['event']=='decision_executed']
        self.assertEqual(proposal['proposed']['price'],850)
        self.assertEqual(executions[0]['executed']['price'],722)
        self.assertEqual(executions[0]['execution_status'],'applied')
        self.assertEqual(executions[-1]['executed']['price'],650)
        self.assertEqual(executions[-1]['execution_status'],'sent')
        self.assertEqual(executions[-1]['decision_id'],proposal['decision_id'])
        self.assertEqual({g['reason'] for g in executions[-1]['guards']},
                         {'opening_offer_range','seller_ask_below_opening_offer'})
        self.assertEqual(report(Path.cwd(),'buyer')['episodes'][0]['status'],'receipt_pending')
        Path('.market').mkdir(exist_ok=True)
        Path('.market/payments.jsonl').write_text(json.dumps({'order':'o1','shop':'https://shop','item':'F1',
                                               'price':650,'paid':True,'ledger':7})+'\n')
        self.assertEqual(buyer_memory.import_payments(),1)
        self.assertEqual(buyer_memory.import_payments(),0)
        review_r3.run({'order':self.order,'receipt':receipt,'settled':{'shop':'https://shop','sku':'F1',
                       'ledger':7,'value':1000,'grade':'A'}},self.catalog)
        journey=report(Path.cwd(),'buyer')['episodes'][0]
        self.assertEqual(journey['status'],'paid');self.assertEqual(journey['ledgers'],[7])
        paid=[r for r in journey['outcomes'] if r['event'] in ('settled','payment')]
        self.assertTrue(all(r['decision_id']==proposal['decision_id'] for r in paid))
        self.assertTrue(all(r['attempt_id']==executions[-1]['attempt_id'] for r in paid))

    def test_send_failure_is_failed_execution_without_a_sent_offer(self):
        responses=[{'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED',
                    'message':{'parts':[{'text':'Asking 650'}]}}},RuntimeError('test transport failure')]
        with patch.object(buyer,'decide_json',return_value=self.answer), \
             patch.object(buyer_r3,'card_endpoint',return_value='https://shop/rpc'), \
             patch.object(buyer_r3,'send',side_effect=responses), \
             patch.object(buyer.buyer_forum_memory,'ingest'), \
             patch.object(buyer.buyer_forum_memory,'retrieve',return_value={}):
            self.assertIsNone(buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0)['receipt'])
        rows=buyer_memory.read_events()
        execution=[r for r in rows if r['event']=='decision_executed']
        self.assertFalse(any(r['execution_status']=='sent' for r in execution))
        self.assertEqual(execution[-1]['execution_status'],'failed')
        self.assertEqual(report(Path.cwd(),'buyer')['episodes'][0]['status'],'unknown')

    def test_fallback_shop_payment_keeps_its_own_attempt_and_parent_decision(self):
        self.shops.append({'name':'other','url':'https://other','offers':[{'sku':'F1','qty':1}]})
        receipt={'item':'F1','price':700,'task':'t2','sig_seller':'fixture'}
        outcomes=[{'receipt':None,'offers':[],'task_id':'t1','reason':'no agreement'},
                  {'receipt':receipt,'offers':[700],'task_id':'t2','reason':'receipt received'}]
        with patch.object(buyer,'decide_json',return_value=self.answer), \
             patch.object(buyer,'negotiate',side_effect=outcomes), \
             patch.object(buyer.buyer_forum_memory,'ingest'), \
             patch.object(buyer.buyer_forum_memory,'retrieve',return_value={}):
            buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0)
        negotiations=[r for r in buyer_memory.read_events() if r['event']=='negotiation']
        first,second=negotiations
        self.assertNotEqual(first['attempt_id'],second['attempt_id'])
        self.assertNotEqual(first['decision_id'],second['decision_id'])
        self.assertEqual(second['parent_decision_id'],first['decision_id'])
        linked=audit.buyer_outcome_links(buyer_memory.read_events(),order='o1',shop='https://other',sku='F1',price=700)
        self.assertEqual(linked['attempt_id'],second['attempt_id'])
        self.assertEqual(linked['decision_id'],second['decision_id'])

    def test_counter_model_clamp_preserves_parent_and_final_receipt_links(self):
        receipt={'item':'F1','price':850,'task':'t1','sig_seller':'fixture'}
        def asking(price):return {'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED',
                                 'message':{'parts':[{'text':f'Asking {price}'}]}}}
        responses=[asking(900),asking(800),{'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},
                     'artifacts':[{'parts':[{'data':receipt}]}]}]
        with patch.object(buyer,'decide_json',return_value=self.answer), \
             patch.object(buyer_r3,'decide_json',return_value={'action':'offer','price':1000,'reason':'test next offer'}), \
             patch.object(buyer_r3,'card_endpoint',return_value='https://shop/rpc'), \
             patch.object(buyer_r3,'send',side_effect=responses), \
             patch.object(buyer.buyer_forum_memory,'ingest'), \
             patch.object(buyer.buyer_forum_memory,'retrieve',return_value={}):
            result=buyer.run(self.order,self.shops,self.catalog,'own',epsilon=0)
        self.assertEqual(result['receipt']['price'],850)
        rows=buyer_memory.read_events()
        proposals=[r for r in rows if r['event']=='decision_proposed']
        self.assertEqual(len(proposals),2)
        self.assertEqual(proposals[-1]['parent_decision_id'],proposals[0]['decision_id'])
        executed=[r for r in rows if r['event']=='decision_executed'][-1]
        self.assertEqual(executed['executed']['price'],850)
        self.assertEqual(executed['proposed']['price'],1000)
        self.assertEqual(executed['guards'][0]['reason'],'authorized_price_range')
        linked=audit.buyer_outcome_links(rows,order='o1',shop='https://shop',sku='F1',price=850,task_id='t1')
        self.assertEqual(linked['decision_id'],proposals[-1]['decision_id'])


if __name__=='__main__':unittest.main()
