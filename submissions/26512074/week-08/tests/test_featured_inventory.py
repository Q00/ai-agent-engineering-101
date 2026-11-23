import json
import tempfile
import unittest
from pathlib import Path
from recommendations import own_featured, NAME
from resale import purchase_intent


class FeaturedInventoryTests(unittest.TestCase):
    def test_public_featured_rows_use_the_same_single_line_format(self):
        from recommendations import readable_post
        body=readable_post(['우리 가게 추천 · 프리미엄 추천: G12345678 Sofa · 확인 호가 123,456 credits · A등급',
                            '우리 가게 추천 · 함께 볼 추천: G87654321 Chair · 가격 문의 · A등급'])
        self.assertIn('• tomato&potato | G12345678 · 호가 123,456 credits',body)
        self.assertIn('• tomato&potato | G87654321 · 호가 미확인 · 견적 요청',body)
        self.assertEqual(len(body.splitlines()),2)

    def test_public_recommendation_prefers_a_real_quote_over_unpriced_stock(self):
        units=[dict(sku='x',name='X',cost=900,list_price=1000,kind='goods'),dict(sku='y',name='Y',cost=10000,list_price=50000,kind='goods')]
        rows=own_featured({'units':units},[dict(sku='x',shop=NAME,price=1000,at=1000)],1000)
        self.assertIn('x X',rows[0])
        self.assertNotIn('가격 문의','\n'.join(rows))

    def test_distinct_price_and_absolute_margin_winners(self):
        units=[dict(sku='x',name='X',cost=900,list_price=1000,kind='goods'),
               dict(sku='y',name='Y',cost=100,list_price=800,kind='goods'),
               dict(sku='z',name='Z',cost=1,list_price=9000,kind='goods',expires_at=999)]
        quotes=[dict(sku='x',shop=NAME,price=1000,at=1000),dict(sku='y',shop=NAME,price=800,at=1000)]
        rows=own_featured({'units':units},quotes,1000)
        self.assertIn('x X',rows[0])
        self.assertIn('y Y',rows[1])
        self.assertNotIn('700',rows[1])
        for word in ('원가','마진','차익','최저 수락가'):
            self.assertNotIn(word,'\n'.join(rows))

    def test_same_winner_is_only_advertised_once_without_estimated_price(self):
        unit=dict(sku='x',name='X',cost=900,list_price=1000,kind='goods',grade='A')
        rows=own_featured({'units':[unit]},[],1000)
        self.assertEqual(len(rows),1)
        self.assertIn('가격 문의',rows[0])
        self.assertNotIn('추정',rows[0])

    def test_only_fresh_known_grade_discount_can_skip_bargaining(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'resale-opportunities.json'
            row=dict(url='https://s',sku='x',quote_at=1000,observed_grade='A',quote=50,median=100)
            path.write_text(json.dumps({'opportunities':[row]}))
            self.assertIsNotNone(purchase_intent(directory,'https://s','x',1000))
            self.assertIsNone(purchase_intent(directory,'https://s','x',1301))
            row['observed_grade']=None
            path.write_text(json.dumps({'opportunities':[row]}))
            self.assertIsNone(purchase_intent(directory,'https://s','x',1000))
