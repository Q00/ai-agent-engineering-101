import time
import unittest
from buyer_compare import compare


class PaidRankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_paid_negotiated_price_beats_cheaper_but_firm_opening_quote(self):
        shops=[dict(url='http://firm',name='firm'),dict(url='http://discount',name='discount')]
        rows=[(0,s,'x',100) for s in shops]
        memory={s['url']:dict(quotes=[dict(sku='x',ask=60 if s['name']=='firm' else 100,
                                          at=time.time(),grade='A',warranty_hours=0)]) for s in shops}
        payments=[dict(shop=s['url'],item='x',grade='A',paid=True,price=55 if s['name']=='firm' else 20)
                  for s in shops for _ in range(2)]
        ranked=await compare(rows,dict(budget=100,kind='goods'),
                             {'x':dict(list_price=100,kind='goods')},memory,payments=payments)
        self.assertEqual(ranked[0][1]['url'],'http://discount')
