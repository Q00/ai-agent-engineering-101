import unittest
from unittest.mock import patch
from recommendations import purchase_guide,render_slate,NAME


class PurchaseGuideTests(unittest.TestCase):
    def fixture(self):
        units=[dict(sku='x',name='X',cost=100,list_price=300,kind='goods',category='sofa',grade='A'),
               dict(sku='y',name='Y',cost=50,list_price=200,kind='goods',category='sofa',grade='B')]
        quotes=[dict(sku='x',shop=NAME,price=300,at=1000,grade='A'),dict(sku='y',shop=NAME,price=200,at=1000,grade='B')]
        cat={u['sku']:dict(u) for u in units}
        return dict(units=units),quotes,cat

    def test_actual_option_prices_and_cheaper_same_category_only(self):
        inv,qs,cat=self.fixture()
        options=[dict(qs[0],warranty_hours=3,price=324),dict(qs[0],warranty_hours=48,price=465,at=0)]
        text='\n'.join(purchase_guide(cat,inv,qs,options,1000))
        self.assertIn('3h 324',text)
        self.assertNotIn('465',text)
        self.assertIn('예산 절약 대안: y',text)
        self.assertIn('요청 예시:',text)

    def test_leader_section_removed_from_full_and_compact_posts(self):
        inv,qs,cat=self.fixture()
        evidence=dict(leaders=[dict(shop='leader-secret',rank=1,all=.9,facts=['old section'],refs=[])],products=[],repeat_pairs=[])
        with patch('recommendations.time.time',return_value=1000):
            for style in ('normal','x'*2100):
                text=render_slate([],cat,inv,style,own_quotes=qs,evidence=evidence)['post']
                self.assertNotIn('leader-secret',text)
                self.assertNotIn('구매 전 체크포인트',text)
                self.assertIn('보증별 가격',text)
                self.assertLessEqual(len(text),2000)
