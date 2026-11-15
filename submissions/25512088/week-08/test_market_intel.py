import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from market_intel import Intel,BUYER_SCORE_WEIGHTS
from seller_strategy import (sale_quote,restock_plan,cost_floor,protected_floor,score_floor,
                             buyer_preference_target,top_seller_grade_target,warranty_hours,
                             score_safe_loss_budget)
from seller_ledger import Program
from buyer_publicist import queue_update,publication_policy,forum_strategy,STRATEGY_REVIEW_SECONDS
from buyer_r3 import Buyer
from buyer_memory import Memory
from seller_market import fetch_shop_reviews,SHOP_REVIEW_FILE


class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.i=Intel(self.root);self.p=Program(self.root)
        self.u=dict(sku='x',unit='u',name='item',kind='goods',grade='A',cost=8000,list_price=10000,expires_at=None)
    def tearDown(self):
        self.p.close();self.i.close();self.tmp.cleanup()
    def rows(self,price=9500,grade='A',expiry=None,warranty=0):
        now=time.time()
        return [dict(seq=n,ts=now,kind='settle',round=3,data=dict(shop='https://shop',sku='x',grade=grade,amount=price,expires_at=expiry,warranty_hours=warranty,claim_honest=True)) for n in range(1,5)]
    def test_idempotent_only_real_round3_settlements(self):
        rows=self.rows();rows += [dict(rows[0],seq=9,round=2),dict(rows[0],seq=10,kind='result')]
        self.i.ingest(rows);self.i.ingest(rows)
        self.assertEqual(self.i.db.execute('select count(*) from trades').fetchone()[0],4)

    def test_live_reliability_and_comparable_queries_are_indexed(self):
        indexes={row[0] for row in self.i.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        self.assertIn('trades_shop_ts_honest',indexes)
        self.assertIn('trades_sku_grade_ts',indexes)
        self.assertIn('observations_shop_ts_kind',indexes)

    def test_fresh_review_cache_fetches_only_new_shop(self):
        now=time.time();cached_at=now-60
        (self.root/SHOP_REVIEW_FILE).write_text(json.dumps({
            'round':3,'fetched_at':cached_at,
            'by_shop':{'old':[{'shop':'old','stars':5,'review':'verified'}]}}))
        called=[]
        class Response:
            def raise_for_status(inner):pass
            def json(inner):return []
        class Client:
            def __enter__(inner):return inner
            def __exit__(inner,*args):pass
            def get(inner,url,params):called.append(params['shop']);return Response()
        shops=[{'name':'old','url':'https://old'},{'name':'new','url':'https://new'}]
        with patch('seller_market.httpx.Client',return_value=Client()):
            rows=fetch_shop_reviews(self.root,'https://market.test',shops,now=now)
        self.assertEqual(called,['new'])
        self.assertEqual(len(rows),1)
        cache=json.loads((self.root/SHOP_REVIEW_FILE).read_text())
        self.assertEqual(cache['fetched_at'],cached_at)
        self.assertEqual(set(cache['by_shop']),{'old','new'})
    def test_ingest_preserves_public_buyer_identity_for_distinct_demand(self):
        rows=self.rows();rows[0]['data']['buyer']='buyer-a'
        self.i.ingest(rows)
        self.assertEqual(self.i.db.execute('select buyer from trades where seq=1').fetchone()[0],'buyer-a')
    def test_official_breakage_reconciles_local_sale_idempotently(self):
        with self.p.db:
            self.p.db.execute('''INSERT INTO sales
                (task,unit,sku,amount,cost,ledger,settled,refunded)
                VALUES(?,?,?,?,?,?,?,0)''',('broken-task','broken-unit','x',9500,8000,77,time.time()))
        event=dict(seq=99,ts=time.time(),kind='broken',round=3,data=dict(
            shop='https://shop',unit='broken-unit',item='x',refunded=9500,
            warranty_hours=6,hours_after_sale=2.1))
        self.i.ingest([event]);self.i.ingest([event])
        self.assertEqual(self.i.db.execute('SELECT COUNT(*) FROM breakages').fetchone()[0],1)
        self.assertEqual(self.p.db.execute(
            "SELECT refunded FROM sales WHERE unit='broken-unit'").fetchone()[0],1)
    def test_price_requires_same_grade_warranty_and_enough_samples(self):
        self.i.ingest(self.rows())
        self.assertEqual(self.i.comparable('x','A')['median'],9500)
        self.assertIsNone(self.i.comparable('x','A')['p10'])
        self.assertIsNone(self.i.comparable('x','B')['median'])
        self.assertIsNone(self.i.comparable('x','A',warranty=6)['median'])
        self.assertIsNone(self.i.comparable('wrong','A')['median'])

    def test_acceptance_first_quote_uses_exact_condition_lower_tail(self):
        now=time.time();rows=[]
        for n,price in enumerate(range(10000,11000,100),1):
            rows.append(dict(seq=100+n,ts=now,kind='settle',round=3,data=dict(
                shop='https://shop',sku='x',grade='A',amount=price,
                warranty_hours=6,claim_honest=True,buyer=f'buyer-{n}')))
        self.i.ingest(rows)
        comp=self.i.comparable('x','A',warranty=6)
        self.assertEqual(comp['p10'],10000)
        q=sale_quote(dict(self.u,cost=1000,list_price=20000),{},self.p,self.i)
        self.assertEqual(q['price'],10000)
        body=json.loads(self.i.db.execute(
            "SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(body['pricing_basis'],'exact_condition_p10')

    def test_live_buyer_preferences_lower_first_quote_without_breaking_floor(self):
        now=time.time()
        prefs={'version':3,'generated_at':now,'buyers':{
            f'b{n}':{'active':True,'skus':[{'sku':'x','median_paid':price,'price_samples':100}]}
            for n,price in enumerate((13000,14000,15000,16000))}}
        target=buyer_preference_target(prefs,'x',now)
        self.assertEqual(target,{'price':13000,'buyers':4,'samples':400,
                                 'basis':'active_buyer_item_lower_quartile'})
        q=sale_quote(dict(self.u,cost=1000,list_price=20000),{},self.p,self.i,
                     buyer_preferences=prefs)
        self.assertEqual(q['price'],13000)
        body=json.loads(self.i.db.execute(
            "SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertTrue(body['buyer_preference_applied'])
        self.assertEqual(body['pricing_basis'],'buyer_preference_lower_quartile')
        expensive=dict(self.u,cost=13000,list_price=20000)
        protected=sale_quote(expensive,{},self.p,self.i,buyer_preferences=prefs)
        self.assertGreater(protected['price'],13000)

    def test_top_seller_grade_target_lowers_sparse_initial_quote(self):
        now=time.time();leaders=[];shops=[];rows=[]
        for n in range(3):
            name=f'leader-{n}';url=f'https://leader-{n}'
            leaders.append({'name':name,'all':1-n*.1});shops.append({'name':name,'url':url})
            for i in range(20):
                rows.append(dict(seq=1000+n*20+i,ts=now,kind='settle',round=3,data=dict(
                    shop=url,sku='other',grade='A',amount=7000,warranty_hours=6,
                    claim_honest=True,buyer=f'b-{n}-{i}')))
        self.i.save_source('board',{'shops':leaders});self.i.save_source('shops',{'shops':shops})
        self.i.ingest(rows)
        catalog={'x':{'kind':'goods','list_price':10000},
                 'other':{'kind':'goods','list_price':10000}}
        target=top_seller_grade_target(self.i,catalog,'A','goods',now)
        self.assertEqual((target['ratio'],target['samples'],target['shops']),(.7,60,3))
        q=sale_quote(dict(self.u,cost=1000),catalog,self.p,self.i)
        self.assertEqual(q['price'],7000)
        bounded=sale_quote(dict(self.u,cost=7200),catalog,self.p,self.i)
        self.assertEqual(bounded['price'],7000)
        self.assertTrue(bounded['loss_leader'])

    def test_buyer_preference_target_requires_three_active_buyers_and_fresh_data(self):
        now=time.time();base={'version':3,'generated_at':now,'buyers':{
            'a':{'active':True,'skus':[{'sku':'x','median_paid':100,'price_samples':10}]},
            'b':{'active':True,'skus':[{'sku':'x','median_paid':110,'price_samples':10}]},
            'offline':{'active':False,'skus':[{'sku':'x','median_paid':1,'price_samples':10}]}}}
        self.assertIsNone(buyer_preference_target(base,'x',now))
        base['buyers']['c']={'active':True,'skus':[{'sku':'x','median_paid':120,'price_samples':10}]}
        self.assertIsNotNone(buyer_preference_target(base,'x',now))
        base['generated_at']=now-181
        self.assertIsNone(buyer_preference_target(base,'x',now))

    def test_small_public_loss_is_bounded_by_buyer_cohort_and_budget(self):
        now=time.time();rows=[]
        for n,price in enumerate(range(10000,11000,100),1):
            rows.append(dict(seq=200+n,ts=now,kind='settle',round=3,data=dict(
                shop='https://shop',sku='x',grade='A',amount=price,
                warranty_hours=6,claim_honest=True,buyer=f'cohort-{n}')))
        self.i.ingest(rows)
        unit=dict(self.u,cost=10300,list_price=20000)
        q=sale_quote(unit,{},self.p,self.i)
        self.assertEqual(q['price'],10000);self.assertTrue(q['loss_leader'])
        self.assertEqual(q['loss_budget_remaining'],99700)
        # A price below the 4% principal-loss bound is never copied.
        unit['cost']=10500
        q=sale_quote(unit,{},self.p,self.i)
        self.assertFalse(q['loss_leader']);self.assertGreater(q['price'],10000)
    def test_loss_budget_uses_only_quarter_of_fresh_official_book_headroom(self):
        self.assertEqual(score_safe_loss_budget(self.i,99999),100000)
        self.i.save_source('board',{'shops':[
            {'name':'below','book':900000,'house':False},
            {'name':'25512088','book':1000000,'house':False},
            {'name':'above','book':2000000,'house':False}]})
        self.assertEqual(score_safe_loss_budget(self.i,99999),124999)
    def test_loss_budget_switches_to_book_after_near_revenue_boundary_is_crossed(self):
        players=[{'student':True,'shop':{'name':name}}
                 for name in ('below','25512088','above')]
        shops=[{'name':'below','book':900000,'revenue':900000,'house':False},
               {'name':'25512088','book':1000000,'revenue':1000000,'house':False},
               {'name':'above','book':1100000,'revenue':2000000,'house':False}]
        self.i.save_source('board',{'players':players,'shops':shops})
        # Weighted Book effort is 100k/.5; Revenue is 1m/.3, so preserve Book.
        self.assertEqual(score_safe_loss_budget(self.i,99999),99999)
        shops[-1]['revenue']=1050000
        self.i.save_source('board',{'players':players,'shops':shops})
        # A nearby Revenue step is cheaper, so the bounded trial remains live.
        self.assertEqual(score_safe_loss_budget(self.i,99999),124999)
    def test_repeated_unconverted_local_offers_lower_opening_ask_without_losing_book(self):
        now=time.time();rows=[]
        for n,price in enumerate(range(10000,11000,100),1):
            rows.append(dict(seq=300+n,ts=now,kind='settle',round=3,data=dict(
                shop='https://shop',sku='x',grade='A',amount=price,
                warranty_hours=6,claim_honest=True,buyer=f'market-{n}')))
        self.i.ingest(rows)
        for n in range(10):
            self.i.decision('seller_quote',{'sku':'x','price':10000})
            self.i.decision('seller_offer_received',{'sku':'x','offered':6000+n})
        self.i.save_source('shops',{'shops':[{'name':'rival','offers':[]}]})
        unit=dict(self.u,cost=5000,list_price=20000)
        q=sale_quote(unit,{},self.p,self.i)
        self.assertEqual(q['price'],6007)
        self.assertGreaterEqual(q['price'],score_floor(unit))
        body=json.loads(self.i.db.execute(
            "SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertTrue(body['own_offer_target_applied'])
        self.assertEqual(body['own_offer_samples'],10)
        self.assertEqual(body['own_offer_target'],6007)
        bounded=sale_quote(dict(unit,cost=6250),{},self.p,self.i)
        self.assertEqual(bounded['price'],6007)
        self.assertTrue(bounded['loss_leader'])
        below_four_percent=sale_quote(dict(unit,cost=6300),{},self.p,self.i)
        self.assertEqual(below_four_percent['price'],6048)
        self.assertTrue(below_four_percent['loss_leader'])
        low_market=[]
        for n in range(10):
            low_market.append(dict(seq=400+n,ts=now,kind='settle',round=3,data=dict(
                shop='https://shop',sku='y',grade='A',amount=5000,
                warranty_hours=6,claim_honest=True,buyer=f'low-market-{n}')))
            self.i.decision('seller_quote',{'sku':'y','price':5000})
            self.i.decision('seller_offer_received',{'sku':'y','offered':6000+n})
        self.i.ingest(low_market)
        direct_offer_evidence=sale_quote(dict(unit,sku='y',cost=6250),{},self.p,self.i)
        self.assertEqual(direct_offer_evidence['price'],6007)
        self.assertTrue(direct_offer_evidence['loss_leader'])
        near_floor_market=[]
        for n in range(10):
            near_floor_market.append(dict(seq=500+n,ts=now,kind='settle',round=3,data=dict(
                shop='https://shop',sku='z',grade='A',amount=5000,
                warranty_hours=6,claim_honest=True,buyer=f'near-floor-{n}')))
            self.i.decision('seller_quote',{'sku':'z','price':7000})
            self.i.decision('seller_offer_received',{'sku':'z','offered':5830+n})
        self.i.ingest(near_floor_market)
        near_floor=sale_quote(dict(unit,sku='z',cost=6250),{},self.p,self.i)
        self.assertEqual(near_floor['price'],6000)
        self.assertTrue(near_floor['loss_leader'])
        body=json.loads(self.i.db.execute(
            "SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(body['loss_leader_evidence'],'own_offer_near_raw_loss_floor')
    def test_current_supply_cohort_is_not_masked_by_old_low_cost_sales(self):
        now=time.time()
        with self.p.db:
            for n in range(10):
                self.p.db.execute(
                    'INSERT INTO sales(task,unit,sku,amount,cost,ledger,settled,refunded) '
                    'VALUES(?,?,?,?,?,?,?,0)',
                    (f'old-{n}',f'old-u-{n}','x',7000,5000,n+1,now-100))
        for n in range(10):
            self.i.decision('seller_quote',{'sku':'x','price':7000})
            self.i.decision('seller_offer_received',{'sku':'x','offered':6000+n})
        unit=dict(self.u,cost=6250,list_price=20000,supplied_at=now-1)
        quote=sale_quote(unit,{},self.p,self.i)
        self.assertEqual(quote['price'],6007)
        self.assertTrue(quote['loss_leader'])
        body=json.loads(self.i.db.execute(
            "SELECT body FROM decisions WHERE role='seller_quote' ORDER BY id DESC LIMIT 1").fetchone()[0])
        self.assertEqual(body['sales_6h'],0)
        self.assertEqual(body['local_evidence_window'],'current_supply_cohort_or_6h')
    def test_food_comparison_life_and_expiry(self):
        self.i.ingest(self.rows(expiry=time.time()+12*3600))
        self.assertEqual(self.i.comparable('x','A',12)['count'],4)
        self.assertIsNone(self.i.comparable('x','A',120)['median'])
        with self.assertRaises(ValueError):sale_quote(dict(self.u,kind='food',expires_at=time.time()-1),{},self.p,self.i)
    def test_market_price_applies_but_never_breaks_floor(self):
        self.i.ingest(self.rows(price=5000))
        q=sale_quote(self.u,{},self.p,self.i)
        self.assertGreaterEqual(q['price'],8720)
        self.assertEqual(q['base'],score_floor(self.u))
    def test_goods_warranty_never_expands_from_unreconciled_local_refund_flag(self):
        with self.p.db:
            for n in range(40):
                self.p.db.execute('INSERT INTO sales(task,unit,sku,amount,cost,ledger,settled,refunded) '
                                  'VALUES(?,?,?,?,?,?,?,0)',
                                  (f't{n}',f'u{n}','x',10000,8000,n+1,time.time()))
        self.assertEqual(warranty_hours(self.u,self.p),6)
    def test_benefit_variants_preserve_net_margin(self):
        # Force founder and referral branches through actual membership records.
        seed=self.p.quote(12000,8000)
        key=self.p.accept('t','unit','sku',8000,seed)
        with self.p.db:self.p.db.execute('update members set active=1,founder=1,credit=1000')
        ref=self.p.db.execute('select code from members').fetchone()[0]
        for cost in (1,8000,20000):
            for token,code in [('', ''),(key,''),('',ref)]:
                q=sale_quote(dict(self.u,cost=cost),{},self.p,self.i,token,code)
                self.assertGreaterEqual(q['price']-q['reward'],cost+__import__('math').ceil(cost*.09))
    @patch("seller_strategy.settings", return_value={"external_campaign": False,"budget":2500000})
    def test_restock_window_cash_evidence_and_stock_limits(self, _policy):
        self.i.ingest(self.rows());self.i.put('ledger_through',time.time())
        inv=dict(round=3,restock={'open':True},balance=5000000,start=5000000,units=[])
        cat={'x':dict(list_price=10000,category='c')}
        self.assertEqual(restock_plan(inv,cat,self.i)['buy'],[{'sku':'x','qty':1}])
        self.assertEqual(restock_plan(dict(inv,restock={'open':False}),cat,self.i)['buy'],[])
        self.assertEqual(restock_plan(dict(inv,balance=500000),cat,self.i)['buy'],[])
        self.assertEqual(restock_plan(dict(inv,units=[self.u]*24),cat,self.i)['buy'],[])
        self.i.put('ledger_through',0)
        self.assertEqual(restock_plan(inv,cat,self.i)['buy'],[])
    def test_failure_deprioritizes_shop(self):
        before=self.i.reliability('url');self.i.observe('url','x','unavailable')
        self.assertLess(self.i.reliability('url'),before)
    def test_live_buyer_score_policy_weights_include_forum_and_posts(self):
        self.assertEqual(BUYER_SCORE_WEIGHTS,{'saved':.50,'forum':.30,'deals':.10,'posts':.10})
    def test_offline_refresh_preserves_data(self):
        self.i.ingest(self.rows());self.i.put('cursor',4)
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503))) as client:
            self.i.refresh('https://market',client)
        self.assertEqual(self.i.get('cursor'),4)
        self.assertEqual(self.i.comparable('x','A')['count'],4)
        self.assertTrue(self.i.get('errors'))
    def test_role_queues_never_cross(self):
        from market_runtime import dispatch_outbox
        from types import SimpleNamespace
        seen=[]
        dispatch_outbox(lambda m,p:seen.append(p),SimpleNamespace(token={'buyer':'secret'}),self.root)
        self.assertEqual(seen,[self.root/'buyer-forum'])
        seen.clear()
        dispatch_outbox(lambda m,p:seen.append(p),SimpleNamespace(token={'shop':'secret'}),self.root)
        self.assertEqual(seen,[self.root])

    def test_buyer_forum_uses_paid_evidence_role_and_rate_limit(self):
        name=self.p.p['shop_name']
        self.i.save_source('board',{'round':3,'buyers':[{'name':name,'deals':20,'posts':0,'forum':.9}]})
        (self.root/'catalog.json').write_text(json.dumps({'x':{'name':'item','list_price':10000}}))
        memory=Memory(self.root)
        with memory.db:
            memory.db.execute('INSERT INTO payments VALUES(?,?,?,?,?,?,?)',('o1','other-shop','x',7000,1,77,None))
            memory.db.execute('INSERT INTO outcomes VALUES(?,?)',('o1',json.dumps({'name':'item','grade':'A','claim_honest':True,'warranty_hours':6})))
        memory.db.close()
        self.assertTrue(queue_update(self.i));self.assertFalse(queue_update(self.i))
        msg=json.loads((self.root/'buyer-forum/outbox.jsonl').read_text())
        self.assertEqual(msg['as'],'buyer');self.assertIn('실제 구매 기록',msg['post']);self.assertEqual(msg['refs'],[77])
        self.assertNotIn('같은 운영자',msg['post']);self.assertIn('보증 6h',msg['post'])
        self.assertFalse((self.root/'outbox.jsonl').exists())

    def test_buyer_forum_fills_missing_market_structure_topic(self):
        name=self.p.p['shop_name'];rows=[]
        for n in range(1,10):
            rows.append(dict(seq=n,ts=time.time(),kind='settle',round=3,data=dict(
                shop=f'https://shop-{n%3}',sku='x',grade='A',amount=7000+n*100,
                warranty_hours=0,claim_honest=True)))
        self.i.ingest(rows)
        self.i.put('post_day',int(time.time()//86400));self.i.put('posts_today',1)
        self.i.save_source('board',{'round':3,'buyers':[{'name':name,'deals':20,'posts':1,'forum':.9}]})
        (self.root/'catalog.json').write_text(json.dumps({'x':{'name':'item','list_price':10000}}))
        self.assertTrue(queue_update(self.i))
        msg=json.loads((self.root/'buyer-forum/outbox.jsonl').read_text())
        self.assertIn('[시장 구조 · 판매처 집중도]',msg['post']);self.assertIn('3개 판매처',msg['post'])
        self.assertEqual(len(msg['refs']),3)

    def test_buyer_publication_policy_accelerates_then_adapts(self):
        board={'buyers':[{'name':'25512088','deals':10,'posts':10,'forum':.881},
                         {'name':'rival-a','deals':10,'posts':100,'forum':.9},
                         {'name':'rival-b','deals':10,'posts':200,'forum':.9}]}
        self.assertEqual(publication_policy(board,'25512088')[:2],(301,287))
        board['buyers'][0]['forum']=.82
        self.assertEqual(publication_policy(board,'25512088')[:2],(900,96))
        board['buyers'][0].update(forum=.9,posts=300)
        self.assertEqual(publication_policy(board,'25512088')[:2],(1800,48))

    def test_forum_strategy_reviews_daily_but_keeps_winner_without_response(self):
        buyer={'forum':.88,'all':.76};theme=forum_strategy(self.i,buyer,'25512088',now=1000)
        same=forum_strategy(self.i,{'forum':.89,'all':.77},'25512088',
                            now=1000+STRATEGY_REVIEW_SECONDS+1)
        self.assertEqual(same,theme)
        state=self.i.get('buyer_forum_strategy');self.assertEqual(state['last_decision'],'KEEP')
        forum=[{'role':'buyer','author':f'rival-{n}','text':f"{__import__('buyer_publicist').FORUM_THEMES[theme]} 분석"}
               for n in range(3)]
        self.i.save_source('forum',forum)
        changed=forum_strategy(self.i,{'forum':.89,'all':.77},'25512088',
                               now=state['started']+STRATEGY_REVIEW_SECONDS+1)
        self.assertNotEqual(changed,theme)
        self.assertEqual(self.i.get('buyer_forum_strategy')['last_decision'],'ROTATE')

if __name__=='__main__':unittest.main()
