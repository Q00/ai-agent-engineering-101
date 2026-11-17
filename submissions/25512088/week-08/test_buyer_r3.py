import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from buyer_memory import Memory
from buyer_r3 import Buyer, asking, candidates, diversify_shop_candidates, limit, stretch_limit, high_middle_limit, middle_limit, upper_bridge_limit, bridge_limit, deals_cap, signed_receipt, exclude_own_shop, quote_rank, execution_order, expected_offer, condition_suitable, probe_exploration_allowed, add_reference_step, EARLY_QUOTE_TARGET, QUOTE_WAVE_SIZE
from buyer_review import review


class BuyerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.m=Memory(self.tmp.name)
        self.o=dict(order='o1',item='김치',budget=10000,acceptable=['SKU1'],kind='food',use_within_hours=48,expires=time.time()+600)
        self.cat={'SKU1':dict(name='김치1',list_price=10000)}
        self.key=Ed25519PrivateKey.generate()
        pub=base64.b64encode(self.key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
        self.shop=dict(name='shop',url='https://shop.test',pubkey=pub,offers=[dict(sku='SKU1',name='김치1',qty=2)])

    def tearDown(self):
        self.m.close(); self.tmp.cleanup()

    def test_probe_exploration_requires_repeat_success_evidence(self):
        self.assertTrue(probe_exploration_allowed(
            {'probability':.5,'trials':2,'wins':1}))
        self.assertTrue(probe_exploration_allowed(
            {'probability':.6,'trials':1,'wins':1}))
        self.assertFalse(probe_exploration_allowed(
            {'probability':.5,'trials':4,'wins':0}))
        self.assertFalse(probe_exploration_allowed(
            {'probability':.49,'trials':20,'wins':10}))

    def test_condition_matched_reference_only_adds_a_better_offer(self):
        steps=[('stretch',6500),('middle',6700),('fallback',7000)]
        self.assertEqual(add_reference_step(steps,6400)[0],('reference',6400))
        self.assertEqual(add_reference_step(steps,6600),steps)

    def receipt(self, price=7000, sku='SKU1'):
        r=dict(shop='shop',item=sku,price=price,task='t1')
        raw=json.dumps(r,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        return dict(r,sig_seller=base64.b64encode(self.key.sign(raw)).decode())

    def test_restarts_never_repeat_an_order(self):
        self.assertTrue(self.m.begin(self.o))
        self.m.state('o1','offer_in_flight'); self.m.close(); self.m=Memory(self.tmp.name)
        self.assertFalse(self.m.begin(self.o))

    def test_payment_only_not_agreement_is_price_evidence(self):
        self.m.begin(self.o)
        self.m.record('o1',self.shop['url'],'SKU1','t1','agreement',7000)
        self.assertIsNone(self.m.reference(self.shop['url'],'SKU1'))
        (Path(self.tmp.name)/'payments.jsonl').write_text(json.dumps(dict(order='o1',shop=self.shop['url'],item='SKU1',price=7000,paid=True,ledger=10))+'\n')
        self.m.sync(); self.m.sync()
        self.assertEqual(self.m.reference(self.shop['url'],'SKU1'),7000)
        self.assertIsNone(self.m.reference(self.shop['url'],'SKU2'))
        self.assertEqual(self.m.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)

    def test_limit_uses_sku_and_grade_not_identical_budget_fraction(self):
        self.assertEqual(limit(self.o,self.cat['SKU1'],'A'),6900)
        self.assertEqual(limit(self.o,self.cat['SKU1'],'C'),4800)
        self.assertEqual(limit(self.o,dict(list_price=2000),'A'),1380)
        self.assertEqual(stretch_limit(self.o,self.cat['SKU1'],'A'),6675)
        self.assertEqual(stretch_limit(self.o,self.cat['SKU1'],'B'),5250)
        self.assertEqual(stretch_limit(self.o,self.cat['SKU1'],'C'),3825)
        self.assertEqual(high_middle_limit(self.o,self.cat['SKU1'],'A'),6762)
        self.assertEqual(high_middle_limit(self.o,self.cat['SKU1'],'B'),5375)
        self.assertEqual(high_middle_limit(self.o,self.cat['SKU1'],'C'),3987)
        self.assertEqual(middle_limit(self.o,self.cat['SKU1'],'A'),6850)
        self.assertEqual(middle_limit(self.o,self.cat['SKU1'],'B'),5500)
        self.assertEqual(middle_limit(self.o,self.cat['SKU1'],'C'),4149)
        self.assertEqual(upper_bridge_limit(self.o,self.cat['SKU1'],'A'),6900)
        self.assertEqual(upper_bridge_limit(self.o,self.cat['SKU1'],'B'),5625)
        self.assertEqual(upper_bridge_limit(self.o,self.cat['SKU1'],'C'),4312)
        self.assertEqual(bridge_limit(self.o,self.cat['SKU1'],'A'),6900)
        self.assertEqual(bridge_limit(self.o,self.cat['SKU1'],'B'),5750)
        self.assertEqual(bridge_limit(self.o,self.cat['SKU1'],'C'),4475)

    def test_quote_comparison_target_matches_observed_conversion_step(self):
        self.assertEqual(EARLY_QUOTE_TARGET,11)
        self.assertEqual(QUOTE_WAVE_SIZE,24)

    def test_candidate_waves_lead_with_distinct_shops_without_dropping_skus(self):
        a={'name':'a','url':'https://a'};b={'name':'b','url':'https://b'};c={'name':'c','url':'https://c'}
        pairs=[(a,{'sku':'a1'}),(a,{'sku':'a2'}),(b,{'sku':'b1'}),
               (c,{'sku':'c1'}),(b,{'sku':'b2'})]
        result=diversify_shop_candidates(pairs)
        self.assertEqual([offer['sku'] for _,offer in result],['a1','b1','c1','a2','b2'])
        self.assertCountEqual([(shop['url'],offer['sku']) for shop,offer in result],
                              [(shop['url'],offer['sku']) for shop,offer in pairs])

    def test_all_shops_with_acceptable_stock_are_visited(self):
        shops=[dict(self.shop,name=str(i)) for i in range(8)]+[dict(self.shop,name='bad',offers=[dict(sku='wrong',qty=1)])]
        self.assertEqual(len(candidates(self.o,shops,self.cat,self.m)),8)

    def test_run_contacts_all_eight_competitors_not_just_top_three(self):
        shops=[dict(self.shop,name=str(i),url='https://shop'+str(i)+'.test') for i in range(8)]
        visited=[]
        def open_quote(shop,offer):
            visited.append(shop['name'])
            return dict(shop=shop,sku=offer['sku'],error='unavailable')
        with httpx.Client() as client:
            buyer=Buyer(self.o,self.m,client,self.cat)
            with patch.object(buyer,'open_session',side_effect=open_quote):
                result=buyer.run(shops)
        self.assertEqual(set(visited),{str(i) for i in range(8)})
        self.assertIsNone(result['receipt'])

    def test_own_shop_excluded_by_name_or_key_before_ranking(self):
        own = dict(self.shop, name='25512088', pubkey='own')
        renamed = dict(own, name='renamed', url='https://new-tunnel.test')
        changed_key = dict(own, pubkey='different')
        shops = exclude_own_shop([own, renamed, changed_key, self.shop], '25512088', 'own')
        self.assertEqual(shops, [self.shop])
        self.assertEqual(candidates(self.o, shops, self.cat, self.m)[0][0], self.shop)

    def test_candidates_compare_all_skus_within_each_shop(self):
        catalog=dict(self.cat,SKU2=dict(name='larger',list_price=20000))
        order=dict(self.o,acceptable=['SKU1','SKU2'],budget=20000)
        shop=dict(self.shop,offers=self.shop['offers']+[dict(sku='SKU2',qty=1)])
        self.assertEqual([o['sku'] for _,o in candidates(order,[shop],catalog,self.m)],['SKU2','SKU1'])

    def test_candidates_compute_shop_conversion_once_for_all_its_skus(self):
        catalog=dict(self.cat,SKU2=dict(name='larger',list_price=20000))
        order=dict(self.o,acceptable=['SKU1','SKU2'],budget=20000)
        shop=dict(self.shop,offers=self.shop['offers']+[dict(sku='SKU2',qty=1)])
        original=self.m.deal_estimate; calls=[]
        with patch.object(self.m,'deal_estimate',side_effect=lambda url:(calls.append(url),original(url))[1]):
            candidates(order,[shop],catalog,self.m)
        self.assertEqual(calls,[self.shop['url']])

    def test_buyer_history_indexes_cover_live_ranking_queries(self):
        indexes={row[0] for row in self.m.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn('events_shop_kind_ts_order',indexes)
        self.assertIn('payments_shop_sku_paid_order',indexes)

    def test_quote_worker_reuses_initialized_schema_and_portfolio(self):
        initialized=[]
        class SecondaryMemory:
            def __init__(inner,root,initialize=True):initialized.append(initialize)
            def key(inner,url):return ''
            def deal_estimate(inner,url):return {'probability':.5,'trials':0,'wins':0}
            def close(inner):pass
        class Card:
            def raise_for_status(inner):pass
            def json(inner):return {'supportedInterfaces':[]}
        class Client:
            def get(inner,*args,**kwargs):return Card()
        buyer=Buyer(self.o,self.m,Client(),self.cat,portfolio={'deals':1})
        with patch('buyer_r3.Memory',SecondaryMemory),patch.object(Buyer,'send',return_value={}):
            buyer.open_session(self.shop,self.shop['offers'][0])
        self.assertEqual(initialized,[False])

    def test_execution_keeps_food_and_every_acceptable_sku_independent_of_our_stock(self):
        order=dict(self.o,acceptable=['SKU1','SKU2'])
        self.assertEqual(execution_order(order),order)
        self.assertEqual(execution_order(order)['kind'],'food')

    def test_expected_surplus_declares_conservative_unmeasured_breakage(self):
        food=expected_offer(self.o,self.cat['SKU1'],7000,.5,.8)
        goods=expected_offer(dict(self.o,kind='goods'),self.cat['SKU1'],7000,.5,.8)
        self.assertEqual(food['breakage_basis'],'conservative-unmeasured')
        self.assertEqual(goods['breakage_probability'],.15)
        self.assertEqual(food['valid_checkout_probability'],.4)
        self.assertGreater(food['expected_surplus'],goods['expected_surplus'])

    def test_run_enumerates_alternative_skus_at_same_shop(self):
        cat=dict(self.cat,SKU2=dict(name='alt',list_price=12000,shelf_hours=72))
        order=dict(self.o,acceptable=['SKU1','SKU2'])
        shop=dict(self.shop,offers=[dict(sku='SKU1',qty=1),dict(sku='SKU2',qty=1)])
        visited=[]
        def opened(_shop,offer):
            visited.append(offer['sku']);return dict(shop=_shop,sku=offer['sku'],error='network',error_type='ReadTimeout')
        self.m.begin(order)
        with httpx.Client() as client:
            buyer=Buyer(order,self.m,client,cat)
            with patch.object(buyer,'open_session',side_effect=opened):buyer.run([shop])
        self.assertEqual(set(visited),{'SKU1','SKU2'})
        self.assertEqual(self.m.db.execute("SELECT COUNT(*) FROM events WHERE kind='network_error'").fetchone()[0],2)

    def test_patch36_margin_saved_beats_absolute_surplus(self):
        cat={'small':dict(list_price=10000),'large':dict(list_price=30000)}
        small=dict(shop=self.shop,sku='small',ask=5000,cap=9000)
        large=dict(shop=self.shop,sku='large',ask=18000,cap=20000)
        self.assertEqual(sorted([small,large],key=lambda s:quote_rank(s,cat))[0],small)
        # An unaffordable quote must not displace an immediately feasible one.
        large['cap']=17000
        self.assertEqual(sorted([small,large],key=lambda s:quote_rank(s,cat))[0],small)

    def test_reliability_weight_can_downrank_high_surplus(self):
        class Evidence:
            def reliability(self,url):return .1 if url=='https://bad.test' else 1
        cat={'small':dict(list_price=10000),'large':dict(list_price=30000)}
        small=dict(shop=self.shop,sku='small',ask=5000,cap=9000)
        large=dict(shop=dict(self.shop,url='https://bad.test'),sku='large',ask=18000,cap=20000)
        self.assertLess(quote_rank(small,cat,Evidence()),quote_rank(large,cat,Evidence()))

    def test_numbers_in_sku_and_expiry_are_not_quotes(self):
        self.assertIsNone(asking('SKU123 10개 48 hours'))
        self.assertEqual(asking('Counteroffer: 7,000.'),7000)
        self.assertEqual(asking('final total 7,100 KRW'),7100)

    def test_deals_cap_enforces_marginal_saved_floor(self):
        portfolio={'deals':1000,'discount_sum':340.0,'saved':340/1100}
        self.assertEqual(deals_cap(self.o,self.cat['SKU1'],'A',portfolio),6900)
        self.assertEqual(deals_cap(self.o,self.cat['SKU1'],'B',portfolio),5875)
        self.assertEqual(deals_cap(self.o,self.cat['SKU1'],'C',portfolio),4637)

    def test_saved_portfolio_uses_only_paid_catalogued_deals(self):
        with self.m.db:
            self.m.db.execute("INSERT INTO payments VALUES('p1','s','SKU1',7000,1,1,NULL)")
            self.m.db.execute("INSERT INTO payments VALUES('p2','s','SKU1',9000,0,2,NULL)")
            self.m.db.execute("INSERT INTO outcomes VALUES(?,?)",('p1',json.dumps({'grade':'A'})))
        portfolio=self.m.saved_portfolio(self.cat)
        self.assertEqual(portfolio['deals'],1)
        self.assertAlmostEqual(portfolio['estimated_margin_saved_sum'],.3/.35)

    def test_receipt_signature_and_exact_sku(self):
        task={'artifacts':[{'parts':[{'data':self.receipt()}]}]}
        self.assertIsNotNone(signed_receipt(task,self.shop,'SKU1',7000,'t1'))
        self.assertIsNone(signed_receipt(task,self.shop,'SKU2',7000,'t1'))
        self.assertIsNone(signed_receipt(task,self.shop,'SKU1',6000,'t1'))
        task['artifacts'][0]['parts'][0]['data']['price']=1
        self.assertIsNone(signed_receipt(task,self.shop,'SKU1',7000,'t1'))

    def test_short_life_quote_is_not_bid_on(self):
        sent=[]
        def handler(req):
            if req.method=='GET':
                return httpx.Response(200,json={'supportedInterfaces':[]})
            body=json.loads(req.content)
            if body['method']=='CancelTask':return httpx.Response(200,json={'result':{}})
            text=body['params']['message']['parts'][0]['text'];sent.append(text)
            return httpx.Response(200,json={'result':{'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED',
                'message':{'parts':[{'text':'grade B, 12 h of shelf life left. Asking 5000.'}]}}}})
        self.m.begin(self.o)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result=Buyer(self.o,self.m,client,self.cat).run([self.shop])
        self.assertIsNone(result['receipt'])
        self.assertEqual(len(sent),1)

    def test_quote_condition_must_be_explicit_and_leave_checkout_time(self):
        self.assertFalse(condition_suitable(self.o,{'hours':None,'warranty':None}))
        self.assertFalse(condition_suitable(self.o,{'hours':48,'warranty':None}))
        self.assertTrue(condition_suitable(self.o,{'hours':48.2,'warranty':None}))
        goods=dict(self.o,kind='goods')
        self.assertFalse(condition_suitable(goods,{'hours':None,'warranty':None}))
        self.assertFalse(condition_suitable(goods,{'hours':None,'warranty':0}))
        self.assertTrue(condition_suitable(goods,{'hours':None,'warranty':6}))

    def test_display_alias_requires_registered_key_and_unchanged_signature(self):
        receipt=dict(shop='Friendly Display Shop',item='SKU1',price=7000,task='t1')
        raw=json.dumps(receipt,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        receipt['sig_seller']=base64.b64encode(self.key.sign(raw)).decode()
        task={'artifacts':[{'parts':[{'data':receipt}]}]}
        self.assertEqual(signed_receipt(task,self.shop,'SKU1',7000,'t1')['shop'],'Friendly Display Shop')
        other=Ed25519PrivateKey.generate()
        wrong=dict(self.shop,pubkey=base64.b64encode(other.public_key().public_bytes(
            serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode())
        self.assertIsNone(signed_receipt(task,wrong,'SKU1',7000,'t1'))
        self.assertIsNone(signed_receipt(task,self.shop,'SKU1',7000,'different-task'))
        receipt['shop']='shop'
        self.assertIsNone(signed_receipt(task,self.shop,'SKU1',7000,'t1'))

    def test_actual_rpc_flow_returns_one_receipt_and_checkout_policy(self):
        requests=[]
        def handler(req):
            if req.method=='GET':
                return httpx.Response(200,json={'supportedInterfaces':[{'url':'https://shop.test/','protocolBinding':'JSONRPC'}]})
            b=json.loads(req.content); requests.append(b)
            text=b.get('params',{}).get('message',{}).get('parts',[{}])[0].get('text','')
            if text=='6900':
                task=dict(id='t1',status={'state':'TASK_STATE_COMPLETED'},artifacts=[{'parts':[{'data':self.receipt(6900)}]}])
            else:
                task=dict(id='t1',status={'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'Grade A. 49 h of shelf life left. Asking 7000'}]}})
            return httpx.Response(200,json={'result':{'task':task}})
        self.m.begin(self.o)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            out=Buyer(self.o,self.m,client,self.cat).run([self.shop])
        self.assertEqual(out['receipt']['item'],'SKU1')
        self.assertEqual(out['accept']['min_hours_left'],48)
        self.assertTrue(out['accept']['cert'])
        self.assertEqual(out['accept']['grades'],['A'])
        self.assertEqual(self.m.db.execute('SELECT state FROM orders').fetchone()[0],'awaiting_payment')
        self.assertIsNone(self.m.reference(self.shop['url'],'SKU1'))

    def test_review_preserves_actual_conditions(self):
        self.m.begin(self.o)
        out=review({'order':self.o,'receipt':self.receipt(),'settled':{'grade':'B','claim_honest':False,'ledger':5}},self.m)
        self.assertEqual(out['stars'],1)
        self.assertEqual(self.m.db.execute('SELECT COUNT(*) FROM outcomes').fetchone()[0],1)

    def test_structured_quote_and_immediate_grade(self):
        from buyer_r3 import quote_fields
        task={'status':{'message':{'parts':[{'data':{'price':7000,'grade':'B','warranty_hours':6}}]}}}
        self.assertEqual(quote_fields(task)['price'],7000)
        self.assertEqual(quote_fields(task)['grade'],'B')
        self.assertEqual(quote_fields(task)['warranty'],6)

    def test_observed_lasts_more_hours_format(self):
        from buyer_r3 import quote_fields
        task={'status':{'message':{'parts':[{'text':'grade C, lasts 8 more hours, market certificate included. Asking 20500.'}]}}}
        self.assertEqual(quote_fields(task)['hours'],8)
        self.assertEqual(asking('grade B | 17.9 h shelf life left | ask 19000'),19000)
        self.assertIsNone(asking('task 12345'))

    def test_rejected_candidate_falls_back_but_unknown_offer_stops(self):
        for uncertain in (False,True):
            with self.m.db:self.m.db.execute('DELETE FROM rival_visits')
            order=dict(self.o,order='fallback'+str(uncertain))
            self.m.begin(order);offers=[]
            other=dict(self.shop,name='other',url='https://other.test')
            def handler(req):
                if req.method=='GET':return httpx.Response(200,json={'supportedInterfaces':[]})
                b=json.loads(req.content)
                if b['method']=='CancelTask':return httpx.Response(200,json={'result':{}})
                text=b['params']['message']['parts'][0]['text']
                if text.isdigit():
                    offers.append(req.url.host)
                    if req.url.host=='shop.test':
                        if uncertain:raise httpx.ReadTimeout('lost',request=req)
                        task={'id':'t1','status':{'state':'TASK_STATE_REJECTED'}}
                    else:task={'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':self.receipt(int(text))}]}]}
                else:task={'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'Grade A. 49 h of shelf life left. Asking '+('6000' if req.url.host=='shop.test' else '6900')}]}}}
                return httpx.Response(200,json={'result':task})
            with httpx.Client(transport=httpx.MockTransport(handler)) as c:
                out=Buyer(order,self.m,c,self.cat).run([self.shop,other])
            self.assertEqual(offers,['shop.test'] if uncertain else ['shop.test','other.test'])
            self.assertEqual(out['receipt'] is None,uncertain)

    def test_negotiation_tries_middle_before_proven_fallback(self):
        amounts=[]
        def handler(req):
            if req.method=='GET':return httpx.Response(200,json={'supportedInterfaces':[]})
            b=json.loads(req.content)
            if b['method']=='CancelTask':return httpx.Response(200,json={'result':{}})
            text=b['params']['message']['parts'][0]['text']
            if text.isdigit():amounts.append(int(text))
            if len(amounts)>=4:
                task={'id':'t1','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':self.receipt(7000)}]}]}
            else:task={'id':'t1','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'Grade A. 49 h of shelf life left. Asking 12000'}]}}}
            return httpx.Response(200,json={'result':task})
        self.m.begin(self.o)
        with httpx.Client(transport=httpx.MockTransport(handler)) as c, patch('buyer_r3.time.sleep'):
            out=Buyer(self.o,self.m,c,self.cat).run([self.shop])
        self.assertEqual(amounts,[6675,6762,6850,6900])
        self.assertTrue(all(a<=6900 for a in amounts))
        self.assertIsNone(out['receipt'])

    def test_quotes_actually_overlap_without_sharing_sqlite(self):
        import threading
        barrier=threading.Barrier(2)
        def handler(req):
            if req.method=='GET':
                barrier.wait(timeout=3)
                return httpx.Response(200,json={'supportedInterfaces':[]})
            b=json.loads(req.content)
            if b['method']=='CancelTask':return httpx.Response(200,json={'result':{}})
            return httpx.Response(200,json={'result':{'id':'t1','status':{'state':'TASK_STATE_REJECTED'}}})
        self.m.begin(self.o)
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            out=Buyer(self.o,self.m,c,self.cat).run([self.shop,dict(self.shop,name='second',url='https://second.test')])
        self.assertIsNone(out['receipt'])
        self.assertEqual(self.m.db.execute("select count(*) from events where kind='unavailable'").fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
