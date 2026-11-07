import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import seller_forum as forum


class ShopForumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ('.market', 'state', 'logs'): (self.root/folder).mkdir()
        self.now = time.time()
        with sqlite3.connect(self.root/'state/seller.sqlite3') as db:
            db.execute('CREATE TABLE leases (unit TEXT, until REAL)')
            db.execute('INSERT INTO leases VALUES (?, ?)', ('held', self.now+1000))
        self.units = [dict(unit=u, sku=s, name='김치', category='식품', kind='food',
                           supplied_at=self.now-5000, expires_at=expiry, grade='B')
                      for u, s, expiry in [('free', 's1', self.now+7200),
                                           ('held', 's2', self.now+7200), ('old', 's3', self.now-1)]]
        (self.root/'.market/inventory.json').write_text(json.dumps({'round': 3, 'units': self.units}))
        os.utime(self.root/'.market/inventory.json', (self.now, self.now))
        (self.root/'.market/forum.json').write_text('[]')
        self.copy = dict(action='post', campaign='spotlight', skus=['s1'], ledger_refs=[],
                         thread_id=None, headline='밥상에 곁들이는 김치', body='필요한 잔여시간과 등급을 알려주세요.', reason='유용한 재고 소개')

    def evidence(self): return forum.facts(self.root, self.now)

    def test_excludes_leased_and_expired_units(self):
        self.assertEqual([s['sku'] for s in self.evidence()['stock']], ['s1'])

    def test_stale_inventory_holds(self):
        with self.assertRaisesRegex(ValueError, 'fresh'): forum.facts(self.root, self.now+40)

    def test_food_life_and_goods_only_warranty_are_disclosed(self):
        text = forum.render(self.copy, self.evidence())
        self.assertIn('B등급·잔여 2시간 이상', text)
        self.assertIn('6시간 보증이 총액에 포함', text)
        self.assertIn('식품은 보증 대상이 아닙니다', text)
        self.assertNotIn('원가', text)

    def test_unverified_refs_and_unavailable_sku_rejected(self):
        for change in ({'ledger_refs': [123]}, {'skus': ['s2']}):
            with self.assertRaises(ValueError): forum.validate(self.copy | change, self.evidence(), [], self.now)

    def test_goods_warranty_request_can_receive_relevant_reply(self):
        self.units[0].update(kind='goods',expires_at=None,name='의자',category='가구')
        (self.root/'.market/inventory.json').write_text(json.dumps({'round':3,'units':self.units}))
        os.utime(self.root/'.market/inventory.json',(self.now,self.now))
        (self.root/'.market/forum.json').write_text(json.dumps([
            {'id':1,'role':'buyer','ts':self.now,'text':'의자 구합니다. 6시간 보증 필요합니다.'},
            {'id':2,'role':'buyer','ts':self.now,'text':'의자 구합니다. 24시간 보증 필요합니다.'}]))
        evidence=self.evidence()
        self.assertEqual([r['id'] for r in evidence['requests']],[1])
        decision=self.copy|{'campaign':'reply','thread_id':1}
        forum.validate(decision,evidence,[],self.now)
        self.assertEqual(forum.reply_stock(evidence,decision)[0]['units'][0]['unit'],'free')

    def test_false_promotion_rejected(self):
        for body in ('육시간 보증', '최저가 할인', '새 상품', '직접써봤어요', '가격은 100원'):
            with self.assertRaises(ValueError): forum.validate(self.copy | {'body': body}, self.evidence(), [], self.now)

    def test_old_stock_cannot_be_new_arrival(self):
        with self.assertRaises(ValueError): forum.validate(self.copy | {'campaign': 'arrival'}, self.evidence(), [], self.now)

    def test_cooldown_prevents_repeating_product(self):
        with self.assertRaises(ValueError): forum.validate(self.copy, self.evidence(), [{'at': self.now-2000, 'skus': ['s1']}], self.now)

    def test_reply_must_match_explicit_buyer_request(self):
        rows = [dict(id=1, role='buyer', ts=self.now, text='김치 필요합니다'),
                dict(id=2, role='shop', ts=self.now, text='김치 필요합니다'),
                dict(id=3, role='buyer', ts=self.now, text='김치 샀는데 불만입니다. 잔여시간이 더 필요했습니다.')]
        (self.root/'.market/forum.json').write_text(json.dumps(rows))
        evidence = self.evidence()
        self.assertEqual([r['id'] for r in evidence['requests']], [1])
        forum.validate(self.copy | {'campaign': 'reply', 'thread_id': 1}, evidence, [], self.now)
        with self.assertRaises(ValueError): forum.validate(self.copy | {'campaign': 'reply', 'thread_id': 2}, evidence, [], self.now)

    def test_atomic_handoff_never_overwrites(self):
        payload = {'post': 'first', 'as': 'shop', 'client_id': 'test'}
        forum.handoff(self.root, payload)
        with self.assertRaises(ValueError): forum.handoff(self.root, payload | {'post': 'second'})
        self.assertEqual(json.loads((self.root/'.market/outbox.jsonl').read_text()), payload)

    def coffee_request(self, hours):
        self.units[0].update(name='바리스타룰스 디카페인 라떼', category='식품>음료>커피/차',
                             expires_at=self.now+hours*3600)
        (self.root/'.market/inventory.json').write_text(json.dumps({'round': 3, 'units': self.units}))
        os.utime(self.root/'.market/inventory.json', (self.now, self.now))
        body = '커피/차 구합니다. 예산은 9만 원대이며 360시간 안에 먹을 예정입니다. 파는 가게가 없어 글 올립니다.'
        (self.root/'.market/forum.json').write_text(json.dumps([dict(id=50, role='buyer', ts=self.now, text=body)]))
        return self.evidence()

    def test_category_request_matches_without_literal_product_name(self):
        evidence = self.coffee_request(400)
        self.assertEqual(evidence['requests'][0]['matching_skus'], ['s1'])
        decision = self.copy | {'campaign': 'reply', 'thread_id': 50}
        forum.validate(decision, evidence, [], self.now)

    def test_category_reply_considers_stock_outside_sourcing_shortlist(self):
        self.units[0].update(name='접이식 매트리스', category='가구>침대/매트리스',
                             kind='goods', expires_at=None, cert='test')
        product = self.units[0] | {'list_price': 100_000}
        catalog = {f'cheap-{i}': product | {'name': f'other {i}', 'list_price': i + 1}
                   for i in range(12)}
        catalog['s1'] = product
        (self.root/'.market/catalog.json').write_text(json.dumps(catalog))
        (self.root/'.market/inventory.json').write_text(json.dumps({'round': 3, 'units': self.units}))
        os.utime(self.root/'.market/inventory.json', (self.now, self.now))
        (self.root/'.market/forum.json').write_text(json.dumps([
            dict(id=50, role='buyer', ts=self.now, text='침대/매트리스 구합니다. 인증서가 있는 B등급 필요합니다.')
        ]))
        request = self.evidence()['requests'][0]
        self.assertNotIn('s1', {match['sku'] for match in request['catalog_matches']})
        self.assertEqual(request['matching_skus'], ['s1'])
        self.assertTrue(request['requirements']['cert_required'])

    def test_extended_reply_slots_only_accept_midprice_quotes(self):
        unit = {'unit': 'free', 'sku': 's1', 'kind': 'goods', 'grade': 'B', 'expires_at': None}
        quote = {'unit': 'free', 'valid_until': self.now + 60, 'warranty_hours': 6}
        options = [quote | {'price': 25_000}, quote | {'price': 80_000}]
        evidence = {'available_units': [unit], 'featured_capacity_left': 1, 'quoted_units': [],
                    'requests': [{'id': 50, 'matching_skus': ['s1'],
                                  'requirements': {'budget_max': 90_000}}],
                    'at': self.now, 'market_dir': self.root/'.market'}
        with patch.object(forum.seller_featured, 'proposals', return_value=options):
            self.assertEqual(forum.reply_quote_options(evidence, forum.REPLY_QUOTE_DAILY_CAP),
                             {50: [options[1]]})
            self.assertEqual(forum.reply_quote_options(evidence, forum.MIDPRICE_REPLY_DAILY_CAP), {})

    def test_short_lived_coffee_is_not_offered_for_360_hour_request(self):
        self.assertEqual(self.coffee_request(164)['requests'], [])

    def test_pet_step_is_not_human_seating(self):
        product = {'sku': 'pet', 'name': '패브릭 반려견 스텝퍼', 'category': '가구>소파/의자/스툴'}
        self.assertFalse(forum.request_matches(product, '소파/의자/스툴 구합니다'))
        self.assertTrue(forum.request_matches(product | {'name': '접이식 스툴'}, '소파/의자/스툴 구합니다'))

    def test_reply_renders_only_units_meeting_request_grade(self):
        evidence = self.coffee_request(400)
        evidence['requests'][0]['requirements']['grades'] = ['B']
        evidence['stock'][0]['units'].append(dict(unit='other', grade='C', expires_at=self.now+500*3600))
        decision = self.copy | {'campaign': 'reply', 'thread_id': 50}
        text = forum.render(decision, evidence)
        self.assertIn('B등급', text)
        self.assertNotIn('C등급', text)
        evidence['stock'][0]['units'][0]['expires_at'] = self.now+100
        with self.assertRaises(ValueError): forum.validate(decision, evidence, [], self.now)

    def test_reply_ignores_advertising_sku_cooldown_but_not_thread_duplicate(self):
        evidence = self.coffee_request(400)
        decision = self.copy | {'campaign': 'reply', 'thread_id': 50}
        prior = [{'at': self.now-100, 'skus': ['s1'], 'thread': None}]
        forum.validate(decision, evidence, prior, self.now)
        with self.assertRaises(ValueError): forum.validate(decision, evidence, prior+[{'thread': 50}], self.now)

    def test_survey_asks_buyers_without_inviting_sellers_to_advertise(self):
        decision = self.copy | {'campaign': 'demand_survey', 'skus': []}
        forum.validate(decision, self.evidence(), [], self.now)
        text = forum.render(decision, self.evidence())
        self.assertIn('찾는 품목·희망 등급', text)
        self.assertNotIn('판매 중인 가게', text)

    def test_queue_then_ack_and_cadence(self):
        decisions = iter([self.copy, {'approve': True, 'unsupported_claims': []}])
        forum.run_once(self.root, decide=lambda *a: next(decisions), now=self.now)
        payload = json.loads((self.root/'.market/outbox.jsonl').read_text())
        self.assertEqual(payload['as'], 'shop')
        state = json.loads((self.root/'state/seller-forum.json').read_text())
        self.assertEqual(state['posts'][0]['status'], 'queued')
        (self.root/'.market/outbox.sent.jsonl').write_text(json.dumps({'sent': payload, 'status': 200, 'answer': '{"post":123}'})+'\n')
        result = forum.run_once(self.root, decide=lambda *a: self.fail('model should not run'), now=self.now+10)
        self.assertEqual(result['reason'], 'cooldown')
        state = json.loads((self.root/'state/seller-forum.json').read_text())
        self.assertEqual(state['posts'][0]['status'], 'published')
        self.assertEqual(state['posts'][0]['post_id'], 123)

    def test_unknown_publication_is_not_retried(self):
        forum.save(self.root/'state/seller-forum.json', {'attempts': [], 'posts': [
            {'id': 'x', 'at': self.now-4000, 'status': 'queued', 'text': 'unknown'}]})
        result = forum.run_once(self.root, decide=lambda *a: self.fail('no retry'), now=self.now)
        self.assertEqual(result['reason'], 'pending publication')

    def test_rejected_copy_never_enters_outbox(self):
        decisions = iter([self.copy, {'approve': False, 'unsupported_claims': ['bad']}])
        with self.assertRaises(ValueError): forum.run_once(self.root, decide=lambda *a: next(decisions), now=self.now)
        self.assertFalse((self.root/'.market/outbox.jsonl').exists())

    def test_invalid_copy_can_be_repaired_once(self):
        decisions = iter([self.copy | {'body': '100원 할인'}, self.copy,
                          {'approve': True, 'unsupported_claims': []}])
        forum.run_once(self.root, decide=lambda *a: next(decisions), now=self.now)
        self.assertTrue((self.root/'.market/outbox.jsonl').exists())

    def test_uncertain_handoff_can_be_confirmed_without_reposting(self):
        entry={'id':'uncertain','at':self.now-100,'status':'handoff_failed','campaign':'spotlight','skus':['s1'],'text':'original'}
        state={'posts':[entry]}
        (self.root/'.market/outbox.sent.jsonl').write_text(json.dumps({'sent':{'client_id':'uncertain'},'status':200,'answer':'{"id":123}'})+'\n')
        forum.reconcile(self.root,state,self.now)
        self.assertEqual(entry['status'],'published')
        self.assertEqual(entry['post_id'],123)

    def test_daily_cap(self):
        forum.save(self.root/'state/seller-forum.json', {'attempts': [], 'posts': [
            {'id': str(i), 'at': self.now-i*330, 'status': 'published', 'text': 'x'} for i in range(forum.DAILY_CAP)]})
        result = forum.run_once(self.root, decide=lambda *a: self.fail('cap'), now=self.now)
        self.assertEqual(result['reason'], 'daily cap')

    def opening(self):
        event = {'id':'open-test','starts_at':self.now,'ends_at':self.now+72*3600}
        forum.save(self.root/'state/seller-opening-event.json',event)
        return event

    def test_opening_banner_has_real_end_time_without_discount_claim(self):
        self.opening()
        decision = self.copy | {'campaign':'opening_event','skus':[], 'headline':'오픈 안내'}
        evidence=self.evidence();forum.validate(decision,evidence,[],self.now)
        text=forum.render(decision,evidence)
        self.assertIn('오픈 안내 기간:',text)
        self.assertIn('가격은 개별 협상',text)
        self.assertNotIn('할인',text)

    def test_opening_automatically_expires_and_cannot_be_extended_by_copy(self):
        event=self.opening()
        self.assertIsNone(forum.opening_event(self.root,self.now-1))
        self.assertIsNone(forum.opening_event(self.root,event['ends_at']))
        self.assertIsNone(forum.opening_event(self.root,event['ends_at']+1))
        decision=self.copy | {'campaign':'opening_event','skus':[]}
        with self.assertRaises(ValueError):forum.validate(decision,self.evidence() | {'opening_event':None},[],event['ends_at'])

    def test_opening_announcement_is_at_most_daily(self):
        self.opening();decision=self.copy | {'campaign':'opening_event','skus':[]}
        prior=[{'campaign':'opening_event','opening_event_id':'open-test','at':self.now-100,'skus':[]}]
        with self.assertRaises(ValueError):forum.validate(decision,self.evidence(),prior,self.now)

    def test_explicit_launch_announcement_respects_market_limit_and_reserves_once(self):
        self.opening()
        forum.save(self.root/'state/seller-forum.json',{'posts':[], 'attempts':[self.now-600]})
        decision=self.copy | {'campaign':'opening_event','skus':[], 'headline':'오픈 안내'}
        calls=iter([decision,{'approve':True,'unsupported_claims':[]}])
        forum.run_once(self.root,now=self.now,decide=lambda *a:next(calls),announce_opening=True)
        state=json.loads((self.root/'state/seller-forum.json').read_text())
        self.assertEqual(state['posts'][-1]['opening_event_id'],'open-test')
        self.assertEqual(forum.run_once(self.root,now=self.now+1,announce_opening=True)['reason'],'pending publication')

    def test_campaign_cannot_claim_a_discount_or_run_beyond_72_hours(self):
        event=self.opening();event['ends_at']+=1
        forum.save(self.root/'state/seller-opening-event.json',event)
        self.assertIsNone(forum.opening_event(self.root,self.now))
        self.opening()
        with self.assertRaises(ValueError):forum.validate(self.copy | {'body':'오픈 할인'},self.evidence(),[],self.now)

    def test_discount_terms_only_appear_when_backend_rate_is_active(self):
        event=self.opening();decision=self.copy | {'campaign':'opening_discount','skus':[]}
        with self.assertRaises(ValueError):forum.validate(decision,self.evidence(),[],self.now)
        event.update(discount_bps=300,discount_starts_at=self.now)
        forum.save(self.root/'state/seller-opening-event.json',event)
        evidence=self.evidence();forum.validate(decision,evidence,[],self.now)
        text=forum.render(decision,evidence)
        self.assertIn('신규 첫 제시가 최대 3% 할인',text)
        self.assertIn('최저판매가 한도',text)
        self.assertIn('중복 자동 할인은 없습니다',text)


if __name__ == '__main__': unittest.main()
