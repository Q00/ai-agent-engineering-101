import json,tempfile,time,unittest
from pathlib import Path
from patch36 import saved_objective
from profit_strategy import replenish
from recommendations import readable_post


class Patch36Tests(unittest.TestCase):
    def test_saved_marginal_gain_can_be_negative_without_hard_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'patch36-scoreboard.json').write_text(json.dumps(dict(at=time.time(),buyer=dict(saved=.3))))
            self.assertLess(saved_objective(.2,.9,d),0)
            self.assertGreater(saved_objective(.5,.9,d),0)

    def test_headings_are_unique_and_marker_is_removed(self):
        ad='【🎁 놓치기 전에 챙기세요! 지금 구매 혜택】'
        body=readable_post(['[매수 기회 포착 | tomato&potato]',ad,'',ad,'【💸 구매 전 가격 비교】','【🍯 상위권 주력 상품 · 수집 정산】'])
        self.assertEqual(body.count(ad),1)
        self.assertNotIn('매수 기회 포착',body)
        self.assertIn('와 이 가격에 사느니 tomato&potato에서 사겠다 ㄹㅇ ㅋㅋ',body)
        self.assertIn('적폐 라인 꿀통! 같이 빨아요~',body)

    def test_surge_adjusted_profit_decides_order_not_popularity(self):
        with tempfile.TemporaryDirectory() as d:
            events=[]
            for sku,price,n in [('hot',140000,20),('next',160000,12)]:
                for i in range(n):
                    events.append(dict(seq=sku+str(i),kind='settle',round=3,ts=10000,data=dict(sku=sku,shop='s'+str(i%3),buyer='b'+str(i%4),amount=price)))
                for i in range(5):
                    events.append(dict(seq=sku+'s'+str(i),kind='supply',round=3,ts=9500,data=dict(sku=sku,shop='s'+str(i%2),total=100000,qty=1)))
            inv=dict(balance=6000000,units=[],restock=dict(open=True),surge=[dict(sku='hot',units=100,factor=1.1)])
            cat={s:dict(kind='goods',list_price=200000) for s in ['hot','next']}
            result=replenish(inv,cat,events,10001,d)
            self.assertEqual(result['buy'][0]['sku'],'next')
            self.assertTrue(any(r['sku']=='hot' for r in result['buy']))
