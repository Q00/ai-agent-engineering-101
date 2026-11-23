import json
from pathlib import Path
import tempfile
import unittest
from shop_promotion import promotion, queue_promotion


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.inv = {'units': [dict(sku='x', name='상품X', expires_at=2000),
                              dict(sku='old', name='만료상품', expires_at=10)]}

    def test_only_live_stock_and_deduplicated_own_sales(self):
        e = dict(seq=1, kind='settle', round=3, ts=900, data={'shop': 'totato2203-r3'})
        post = promotion(self.inv, [e, e, {**e, 'seq': 2, 'data': {'shop': 'other'}}], 1000, 2)
        self.assertIn('정산 1건', post['post'])
        self.assertNotIn('만료상품', post['post'])
        self.assertEqual(post['refs'], [1])
        self.assertIn('감사',promotion({'units': []}, [], 1000)['post'])
        for edition in range(3):
            self.assertNotIn('상품X',promotion(self.inv,[],1000,edition)['post'])
            self.assertNotIn('현재 판매 재고',promotion(self.inv,[],1000,edition)['post'])

    def test_no_popularity_or_sales_invented(self):
        text = promotion(self.inv, [], 1000, 2)['post']
        self.assertIn('확인되지 않았습니다', text)
        self.assertNotIn('웨이팅', text)
        self.assertNotIn('유행', text)

    def test_interval_and_pending_queue(self):
        with tempfile.TemporaryDirectory() as d:
            md = Path(d)
            self.assertFalse(queue_promotion(md, self.inv, [], 1000))
            self.assertTrue(queue_promotion(md, self.inv, [], 1000, True))
            self.assertFalse(queue_promotion(md, self.inv, [], 1301, True))
            (md / 'outbox.jsonl').unlink()
            self.assertFalse(queue_promotion(md, self.inv, [], 1100, True))
            self.assertTrue(queue_promotion(md, self.inv, [], 1301, True))
            state = json.loads((md / 'promotion-state.json').read_text(encoding='utf-8'))
            self.assertEqual(state['edition'], 2)


if __name__ == '__main__':
    unittest.main()
