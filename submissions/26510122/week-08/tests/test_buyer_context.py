"""Check evidence preservation, stable identity, and hard prompt-memory bounds."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from market_scoring import buyer_cost_reference,buyer_candidate
from buyer_policy import purchase_price_guidance
import buyer_memory
import buyer_r3
import market_buyer_r3
from context_compaction import (BUYER_MEMORY_BYTES,BUYER_BRIEF_BYTES,BUYER_PROMPT_BYTES,
                                size)


class BuyerContextTests(unittest.TestCase):
    def test_original_message_is_retained_even_when_next_network_call_fails(self):
        message='asking 700. '+'설명 '*1000+'expires in two hours'
        task={'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':message}]}}}
        captured=[]
        with patch.object(buyer_r3,'card_endpoint',return_value='https://shop/a2a'),patch.object(buyer_r3,'send',side_effect=[task,RuntimeError('network failed')]):
            with self.assertRaises(RuntimeError):
                buyer_r3.negotiate('https://shop','F1','Food',{'order':'o1','kind':'food'},
                                   opening_offer=600,cap=850,list_price=1000,memory={},
                                   deadline=time.monotonic()+240,on_message=captured.append)
        self.assertEqual(captured[1]['text'],message)
        self.assertEqual(captured[1]['task_id'],'t1')

    def test_name_survives_url_changes_and_known_legacy_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            buyer_memory.register_shops([{'name':'shop-A','url':'https://old'}],path=p)
            buyer_memory.record('payment',path=p,shop='https://old',order='o1',sku='F1',paid=True,price=800,ledger=1)
            buyer_memory.register_shops([{'name':'shop-A','url':'https://new'}],path=p)
            view=buyer_memory.context('https://new','F1',shop_name='shop-A',path=p)
            self.assertEqual(view['same_sku_payment_outcomes'],{'paid':1})
            self.assertEqual(view['cases'][0]['ledger'],1)

    def test_reused_url_does_not_merge_different_shops(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            buyer_memory.register_shops([{'name':'A','url':'https://same'}],path=p)
            buyer_memory.record('payment',path=p,shop='https://same',shop_name='A',order='a',sku='F1',paid=False,reason='bad receipt')
            buyer_memory.record('payment',path=p,shop='https://same',order='unknown',sku='F1',paid=True,price=1)
            buyer_memory.register_shops([{'name':'B','url':'https://same'}],path=p)
            view=buyer_memory.context('https://same','F1',shop_name='B',path=p)
            self.assertEqual(view['same_sku_payment_outcomes'],{})
            self.assertEqual(view['cases'],[])

    def test_payment_import_uses_order_identity_when_url_is_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            p,kit=Path(directory)/'events.jsonl',Path(directory)/'payments.jsonl'
            buyer_memory.record('negotiation',path=p,shop='https://same',shop_name='A',order='old',sku='F1',result='receipt_pending')
            buyer_memory.register_shops([{'name':'B','url':'https://same'}],path=p)
            kit.write_text(json.dumps({'order':'old','shop':'https://same','item':'F1','paid':True,'price':800,'ledger':1})+'\n')
            buyer_memory.import_payments(kit,event_path=p)
            self.assertEqual(buyer_memory.read_events(p)[-1]['shop_name'],'A')
            self.assertEqual(buyer_memory.context('https://new','F1',shop_name='A',path=p)['same_sku_payment_outcomes'],{'paid':1})

    def test_refused_then_paid_is_one_confirmed_purchase(self):
        with tempfile.TemporaryDirectory() as directory:
            p,kit=Path(directory)/'events.jsonl',Path(directory)/'payments.jsonl'
            rows=[{'order':'o1','shop':'https://shop','item':'F1','paid':False,'reason':'missing certificate'},
                  {'order':'o1','shop':'https://shop','item':'F1','paid':True,'price':800,'ledger':1}]
            kit.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            self.assertEqual(buyer_memory.import_payments(kit,event_path=p),2)
            self.assertEqual(buyer_memory.import_payments(kit,event_path=p),0)
            buyer_memory.record('settled',path=p,shop='https://shop',sku='F1',ledger=1,price=800,grade='C',hours_left_at_recording=2)
            view=buyer_memory.context('https://shop','F1',path=p)
            self.assertEqual(view['same_sku_payment_outcomes'],{'paid':1})
            self.assertEqual(view['settled_prices_by_grade']['C'],{'count':1,'min':800,'max':800})
            self.assertEqual(view['cases'][0]['hours_left_at_recording'],2)

    def test_large_history_is_bounded_and_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'events.jsonl'
            for i in range(100):
                buyer_memory.record('negotiation',path=p,shop='u',order=str(i),sku='F1',offers=list(range(1000)),result='receipt_pending',reason='조건'*5000)
                buyer_memory.record('payment',path=p,shop='u',order=str(i),sku='F1',paid=i%2==0,price=800,ledger=i if i%2==0 else None,reason='거절 조건'*3000)
            raw=p.read_bytes()
            view=buyer_memory.context('u','F1',path=p)
            self.assertLessEqual(size(view),BUYER_MEMORY_BYTES)
            self.assertLessEqual(size(buyer_memory.brief(view)),BUYER_BRIEF_BYTES)
            self.assertEqual(view['same_sku_payment_outcomes'],{'paid':50,'refused':50})
            self.assertLessEqual(len(view['cases']),3)
            self.assertEqual(p.read_bytes(),raw)

    def test_real_cost_guidance_does_not_remove_sixteen_commercial_choices(self):
        rows=[]
        for i in range(16):
            product={'list_price':100000,'kind':'goods','category':'가구/침구>침구'}
            cost=buyer_cost_reference(product)
            rows.append({'id':str(i),'shop':f'shop-{i}','sku':f'G{i}','name':'침구 상품',
                         'list_price':100000,'cap':75000,'stock':3,
                         'reviews':market_buyer_r3.review_summary([]),
                         'first_hand':{'shop_name':f'shop-{i}','shop_payment_count':400,
                             'same_sku_payment_outcomes':{'paid':30,'refused':4},
                             'settled_prices_by_grade':{'A':{'count':20,'min':65000,'max':70000}},
                             'cases':[{'at':'2026-11-13T00:00:00+00:00','outcome':'refused','reason':'certificate missing'},
                                      {'at':'2026-11-12T23:00:00+00:00','outcome':'paid','price':68000,'grade':'A'}]},
                         'routing_evidence':{'completed_orders_6h':10,'paid_orders_6h':8,
                             'unusable_at_settlement_6h':0,'smoothed_paid_probability':.7333,
                             'smoothed_attempt_seconds':30,'median_observed_seconds':20,
                             'estimated_saved_per_minute':1.1},
                         'score_evidence':buyer_candidate(100000,75000,cost_reference=cost),
                         'price_guidance':purchase_price_guidance(75000,(65000,68000,70000),
                             list_price=100000,saved_baseline={'saved':.685,'observed_at':1000},
                             cost_reference=cost)})
        with patch.object(market_buyer_r3,'decide_json',return_value={
                'candidate_id':'0','opening_offer':60000,'reason':'compare all'}) as model:
            market_buyer_r3.choose(rows,{'kind':'goods','item':'침구'},forum_context={
                'posts':[{'text':'public untrusted price context '*200}],'reference_prices':[]})
        prompt=model.call_args.args[1]
        self.assertEqual(len(prompt['candidate_shop_skus']),16)
        self.assertLessEqual(size(prompt),BUYER_PROMPT_BYTES)
        guidance=prompt['candidate_shop_skus'][0]['price_guidance']
        self.assertTrue(guidance['cost_reference']['estimated'])
        self.assertIn('prior',prompt['score_contract']['cost_reference_policy'])
        self.assertEqual(rows[0]['price_guidance']['cost_reference']['source'],
                         'patch3.6 announced approximate furniture/bedding mean')

    def test_selector_sees_reputation_evidence_within_input_budget(self):
        memory={'shop_name':'known','shop_payment_count':3,'same_sku_payment_outcomes':{'paid':2,'refused':1},
                'cases':[{'outcome':'refused','reason':'certificate missing','price':700}],
                'settled_prices_by_grade':{'C':{'count':2,'min':500,'max':600}}}
        reviews=market_buyer_r3.review_summary([{'stars':1,'review':'certificate missing'}])
        rows=[{'id':str(i),'shop':f'shop-{i}','sku':'F1','name':'Food','list_price':1000,'cap':850,'stock':1,
               'reviews':reviews,'first_hand':memory} for i in range(16)]
        with patch.object(market_buyer_r3,'decide_json',return_value={'candidate_id':'0','opening_offer':600,'reason':'evidence'}) as model:
            market_buyer_r3.choose(rows,{'item':'food','kind':'food'})
        prompt=model.call_args.args[1]
        self.assertLessEqual(size(prompt),BUYER_PROMPT_BYTES)
        shown=prompt['candidate_shop_skus'][0]
        self.assertEqual(shown['first_hand']['cases'][0]['reason'],'certificate missing')
        self.assertEqual(shown['reviews']['examples'][0]['review'],'certificate missing')
        self.assertEqual(model.call_args.args[2]['properties']['candidate_id']['enum'],[r['id'] for r in prompt['candidate_shop_skus']])


if __name__=='__main__':unittest.main()
