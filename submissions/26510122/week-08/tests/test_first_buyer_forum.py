import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import seller_demand
import seller_forum as forum


class FirstBuyerForumTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.runtime = Path(temporary.name)
        self.now = time.time()
        for name in ('.market', 'state', 'logs'):
            (self.runtime / name).mkdir()
        with sqlite3.connect(self.runtime / 'state/seller.sqlite3') as db:
            db.execute('CREATE TABLE leases (unit TEXT, until REAL)')
        self.product = {'name': '스탠드 조명', 'kind': 'goods', 'category': '조명', 'list_price': 80_000}
        self.catalog = {'Gdemo': self.product}
        self.write_market('catalog.json', self.catalog)
        self.write_market('inventory.json', {'round': 3, 'units': [
            {**self.product, 'sku': 'Gdemo', 'unit': 'u-one', 'cost': 30_000,
             'grade': 'A', 'expires_at': None}]})
        self.write_market('forum.json', [])

    def write_market(self, name, data):
        p = self.runtime / '.market' / name
        p.write_text(json.dumps(data, ensure_ascii=False))
        os.utime(p, (self.now, self.now))

    def model(self, role, prompt, schema):
        if 'checker' in role:
            return {'approve': True, 'unsupported_claims': []}
        facts = prompt['facts']
        campaign, = schema['properties']['campaign']['enum']
        thread = None
        if campaign == 'reply':
            thread, = facts['reply_quotes']
            choices = facts['reply_quotes'][thread]
        else:
            choices = facts['featured_quotes']
        return {'action': 'post', 'campaign': campaign, 'thread_id': thread, 'skus': ['Gdemo'],
                'offer_ids': [choices[0]['id']], 'ledger_refs': [], 'headline': '조명 견적',
                'body': '현재 재고의 구매 조건을 안내합니다.', 'reason': 'verified stock'}

    def test_new_buyer_keeps_reserved_slot_at_general_cap(self):
        path = self.runtime / 'state/seller-forum.json'
        forum.save(path, {'posts': [
            {'id': str(i), 'at': self.now - 5400, 'campaign': 'reply', 'thread': i + 1,
             'offer_ids': ['old'], 'status': 'published', 'skus': []}
            for i in range(forum.MIDPRICE_REPLY_DAILY_CAP)], 'attempts': []})
        self.write_market('forum.json', [
            {'id': 99, 'role': 'buyer', 'author': 'buyer-new', 'ts': self.now, 'text': 'Gdemo 견적 주세요. 예산 5만원.'}
        ])
        with patch.object(forum.seller_featured, 'active', return_value=[{'unit': 'held-a'}, {'unit': 'held-b'}]):
            forum.run_once(self.runtime, now=self.now, decide=self.model)
        post = json.loads(path.read_text())['posts'][-1]
        self.assertEqual(post['thread'], 99)
        self.assertTrue(post['first_buyer_recipient'])
        self.assertTrue(post['no_prior_paid_link'])

    def test_no_eligible_new_request_posts_one_public_first_checkout_quote(self):
        forum.save(self.runtime / 'state/counterparty-ledger.json', {'recent_purchases': [
            {'seq': 42, 'at': self.now - 30, 'buyer': 'buyer-new', 'sku': 'Gdemo', 'price': 38_000,
             'grade': 'A', 'warranty_hours': 6, 'remaining_seconds': None}]})
        result = forum.run_once(self.runtime, now=self.now, decide=self.model)
        self.assertEqual(result['decision']['campaign'], 'featured')
        self.assertIn('스탠드 조명 · 최대 ', result['text'].splitlines()[0])
        self.assertIn('견적코드 FO-', result['text'])
        self.assertIn('저희 가게가 처음이라면', result['text'])
        posts = json.loads((self.runtime / 'state/seller-forum.json').read_text())['posts']
        self.assertTrue(posts[0]['first_checkout'])

    def test_checkout_candidate_requires_real_demand_and_safe_comparable_price(self):
        facts = forum.facts(self.runtime, self.now)
        market = {'seq': 42, 'at': self.now - 30, 'buyer': 'buyer-new', 'sku': 'Gdemo',
                  'price': 34_500, 'grade': 'A', 'warranty_hours': 6, 'remaining_seconds': None}
        facts['recent_market_purchases'] = [market]
        quote, = forum.first_checkout_quotes(facts, set())
        self.assertEqual(quote['price'], 34_500)
        self.assertGreater(quote['price'], quote['price_floor'])
        self.assertEqual(forum.first_checkout_quotes(facts, {'Gdemo'}), [])
        facts['recent_market_purchases'] = [market | {'price': 30_000}]
        self.assertEqual(forum.first_checkout_quotes(facts, set()), [])
        facts['recent_market_purchases'] = [market]
        facts['known_paid_buyers'] = {'buyer-new'}
        self.assertEqual(forum.first_checkout_quotes(facts, set()), [])

    def test_purchase_report_is_not_intent_and_first_checkout_comment_can_opt_in(self):
        forum.save(self.runtime / 'state/seller-forum.json', {'posts': [
            {'campaign': 'featured', 'status': 'published', 'post_id': 45, 'first_checkout': True}]})
        common = {'role': 'buyer', 'author': 'buyer-new', 'ts': self.now}
        self.write_market('forum.json', [
            {**common, 'id': 46, 'text': '[실제 구매 기록] Gdemo 정산 금액 확인. 후속 고장 여부는 별도 검증이 필요합니다.'},
            {**common, 'id': 47, 'thread': 45, 'text': 'Gdemo · A등급 · 예산 5만원'},
        ])
        seller_demand.ingest(self.runtime, self.catalog, self.now)
        self.assertEqual([r['id'] for r in seller_demand.active(self.runtime, self.now)], [47])
