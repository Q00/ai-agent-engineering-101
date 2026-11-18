import unittest
import tempfile
from pathlib import Path
import recommendations as r

class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.now=100000
        self.events=[{'seq':i,'kind':'settle','round':3,'ts':self.now-i*30,
                      'data':{'item':'x','amount':100,'shop':'other'}} for i in range(10)]
    def test_real_windows_dedup_and_reliability(self):
        groups=r.trades(self.events+self.events,self.now)
        self.assertEqual(len(groups['x']),10)
        f=r.features(groups['x'],2,[],'x',self.now)
        self.assertEqual(f['median'],100)
        self.assertIsNone(f['volume_growth'])
        q={'sku':'x','shop':r.NAME,'url':'http://shop','price':70,'at':self.now}
        self.assertGreater(r.score(f,q,self.now)['score'],12)
        self.assertIsNone(r.score(f,{**q,'price':100},self.now))
        self.assertIsNone(r.score(f,{**q,'at':self.now-301},self.now))
        self.assertIsNone(r.features(self.events[:2],2,[],'x',self.now))
    def test_post_includes_conditions_ad_and_source(self):
        f=r.features(self.events,2,[],'x',self.now)
        q={'sku':'x','shop':r.NAME,'url':'http://shop','price':70,'at':self.now}
        rec=r.score(f,q,self.now)
        post=r.render(rec,{'x':{'name':'X'}},{'units':[{'sku':'y','name':'Y'}]},'가격 메리트')
        self.assertIn('30.0%',post['post'])
        self.assertIn('우리 가게',post['post'])
        self.assertNotIn('좋아요',post['post'])
        self.assertIn('이전 기간 거래 없음',post['post'])
        self.assertEqual(len(post['refs']),5)
    def test_feedback_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            c=r.database(d)
            import json
            data={'score':25,'median':100,'price':70,'shop_profit_baseline':0}
            c.execute('insert into publications values(?,?,?,?,?,?,null)',('h',98000,'x','가격 메리트','body',json.dumps(data)))
            sent=[{'sent':{'post':'body'},'status':200,'answer':'{"post":3}'}]
            r.measure_feedback(c,self.events,[{'id':3,'ts':98000,'likes':2,'dislikes':0}],sent,100000,{'balance':5000000,'start':5000000})
            c.commit();c.close()
            c=r.database(d)
            f=json.loads(c.execute('select feedback from publications').fetchone()[0])
            self.assertTrue(f['mature'])
            self.assertEqual(f['likes'],2)
            self.assertEqual(f['status'],'published')
            self.assertIsNone(f['visits'])
            c.close()
    def test_slate_three_distinct_shops_and_short_final_ad(self):
        f=r.features(self.events,2,[],'x',self.now)
        base=r.score(f,{'sku':'x','shop':r.NAME,'url':'http://shop','price':70,'at':self.now},self.now)
        results=[{**base,'sku':str(i),'shop':'shop'+str(i),'score':30-i} for i in range(7)]
        results.append({**results[0],'score':10})
        with tempfile.TemporaryDirectory() as d:
            c=r.database(d)
            slate=r.select_slate(results,c,lambda item:item['score'])
            self.assertEqual([s['sku'] for s in slate],['0','1','2'])
            post=r.render_slate(slate,{str(i):{'name':'Product '+str(i)} for i in range(7)}, {'units':[]},'가격 메리트')
            for i in range(3):
                self.assertIn('Product '+str(i),post['post'])
            self.assertNotIn('Product 5',post['post'])
            self.assertIn('\n\n【🎁 놓치기 전에 챙기세요! 지금 구매 혜택】\ntomato&potato:',post['post'])
            self.assertIn('【🤝 이 가격에 팔면 뭐가 남나요? 바로 여러분】',post['post'])
            self.assertIn('30.0%',post['post'])
            c.close()
    def test_partial_evidence_publishes_without_fabricating_three_shops(self):
        import io,json
        from unittest.mock import patch,AsyncMock
        with tempfile.TemporaryDirectory() as d:
            md=Path(d)
            catalog={str(i):{'name':'Product '+str(i)} for i in range(3)}
            events=[{**e,'seq':i*100+e['seq'],'data':{**e['data'],'item':str(i)}}
                    for i in range(3) for e in self.events]
            (md/'catalog.json').write_text(json.dumps(catalog))
            (md/'ledger.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            shops={'shops':[{'name':'other','url':'http://shop','stock':{str(i):2 for i in range(3)}}]}
            quotes=[{'sku':str(i),'shop':'other','url':'http://shop','price':70,'at':self.now} for i in range(3)]
            with patch.object(r.time,'time',return_value=self.now), \
                 patch.object(r,'urlopen',return_value=io.BytesIO(json.dumps(shops).encode())), \
                 patch.object(r,'fetch_quotes',new=AsyncMock(return_value=quotes)):
                result=r.tick(md,enabled=True)
            self.assertEqual(result['status'],'queued')
            self.assertEqual(result['recommendation_count'],1)
            post=json.loads((md/'outbox.jsonl').read_text(encoding='utf-8'))
            self.assertIn('【🤝 이 가격에 팔면 뭐가 남나요? 바로 여러분】',post['post'])
            self.assertIn('【💸 와 이 가격에 사느니 tomato&potato에서 사겠다 ㄹㅇ ㅋㅋ】',post['post'])
    def test_exclusions_use_actual_recent_evidence_and_no_positive_overlap(self):
        f=r.features(self.events,2,[],'x',self.now)
        shops=[{'name':'bad','url':'http://bad'},{'name':'expired','url':'http://expired'}]
        audits={'http://bad':{'avoid_until':self.now+100,'reason':'receipt violation: price exceeds offer'},
                'http://expired':{'avoid_until':self.now-1,'reason':'receipt violation: price exceeds offer'}}
        quotes=[{'sku':'x','shop':name,'url':'http://'+name,'price':price,'at':at}
                for name,price,at in [('expensive',150,self.now),('fair',105,self.now),
                                     ('stale',200,self.now-301),('positive',200,self.now)]]
        excluded=r.purchase_exclusions(quotes,{'x':f},audits,shops,[{'shop':'positive'}],self.now)
        self.assertEqual({s['shop'] for s in excluded},{'bad','expensive'})
if __name__=='__main__':
    unittest.main()
