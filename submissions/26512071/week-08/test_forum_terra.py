"""terra-forum-v1: facts -> Terra publish/abstain -> durable intent -> outbox -> ack -> readback. Offline fixtures."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forum_r3
import forum_terra
import review_r3

HERE = Path(__file__).resolve().parent
LEDGER = 5_500_001
SHOP = 'https://shop.example'


def settle(seq=LEDGER, amount=7000, item='G1', buyer='fair-furniture', order='o-1', grade='A', warranty=None, ts=1_800_000_000.0):
    return {'seq':seq, 'kind':'settle', 'round':3, 'ts':ts,
            'data':{'amount':amount, 'buyer':buyer, 'shop':SHOP, 'order':order, 'item':item, 'sku':item, 'grade':grade,
                    'claim':grade, 'claim_honest':True, 'warranty_hours':warranty, 'name':'테스트 사과 1kg',
                    'expires_at':ts+30*3600}}


COMMENT = '같은 상품을 고민하는 분께 참고가 되도록 제 실제 결제 내역을 공유합니다.'
CAVEATS = ('내 개인 결제 1건 기준', '시장 전체 가격이 아닙니다', '내구성은 확인되지 않았고 보장하지 않습니다')


def text_ok(ledger=LEDGER):
    return COMMENT


def terra(action='publish', text=None, error=None, returned='gpt-6.1-terra'):
    def call(prompt):
        call.prompts.append(prompt)
        if error:
            return {'text':'', 'error':error, 'requested':'gpt-6.1-terra', 'returned':None}
        return {'text':json.dumps({'action':action, 'comment':text if text is not None else COMMENT, 'reason':'useful'}),
                'requested':'gpt-6.1-terra', 'returned':returned, 'http_status':200,
                'usage':{'input':300, 'output':80, 'reasoning':0}, 'latency_s':1.1, 'finish_reason':'stop', 'error':None}
    call.prompts = []
    return call


class ForumTerraTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Path(self._tmp.name)
        self.now = 1_800_000_100.0
        (self.s/forum_terra.CONFIG).write_text(json.dumps({'enabled':True}), encoding='utf-8')
        self.pay(LEDGER)
        self.catalog = {'G1':{'name':'테스트 사과 1kg', 'list_price':10000}}
        self.old = {'version':1, 'intents':{}, 'content':[]}
        self.write_old()

    def tearDown(self):
        self._tmp.cleanup()

    def pay(self, ledger, price=7000, order='o-1'):
        with open(self.s/'payments.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'paid':True, 'ledger':ledger, 'price':price, 'order':order, 'shop':SHOP, 'item':'G1'})+'\n')

    def write_old(self):
        (self.s/'forum-report.json').write_text(json.dumps(self.old), encoding='utf-8')

    def post(self, call, rows=None, now=None):
        return forum_terra.maybe_post(self.s, now=now or self.now, rows=rows if rows is not None else [settle()],
                                      call=call, catalog=self.catalog)

    def record(self):
        return json.loads((self.s/forum_terra.RECORD).read_text(encoding='utf-8'))

    def outbox(self):
        p = self.s/'outbox.jsonl'
        return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []

    def test_modules_are_the_candidate(self):
        for m in (forum_terra, forum_r3, review_r3):
            self.assertEqual(Path(m.__file__).resolve().parent, HERE)

    def test_publish_queues_exact_line_after_durable_intent(self):
        call = terra()
        got = self.post(call)
        self.assertTrue(got['queued'], got)
        i = self.record()['intents'][str(LEDGER)]
        post = COMMENT+'\n'+forum_terra.fact_block(i['facts'])
        self.assertEqual(self.outbox(), [{'post':post, 'refs':[LEDGER], 'as':'buyer'}])
        for c in CAVEATS+('#%d' % LEDGER, '7,000원', '10,000원', '3,000원(30.0%)', '등급 A', '관측 '):
            self.assertIn(c, post)
        self.assertEqual(i['status'], 'queued')
        self.assertEqual(i['facts']['saving'], 3000)
        self.assertEqual(i['facts']['saving_pct'], 30.0)
        self.assertEqual(i['model']['returned'], 'gpt-6.1-terra')
        self.assertEqual(i['post_sha256'], hashlib.sha256(post.encode()).hexdigest())
        self.assertIn('"saving": 3000', call.prompts[0])  # arithmetic by code, given to the model

    def test_model_arithmetic_or_accusation_is_rejected_fail_closed(self):
        for bad in ('3,100원 아꼈어요. '+COMMENT, COMMENT+' 다른 상점은 사기입니다.', 'Great saving here, honest shop.',
                    COMMENT+' 추천 부탁드려요', '시장 최저가로 샀어요.', '내구성 보장되는 좋은 상품입니다.',
                    '가장 튼튼한 소파예요.', '이 가격이면 best price 입니다.', '원장 번호를 꼭 확인하세요 #%d' % LEDGER):
            with self.subTest(bad=bad):
                self.tearDown(); self.setUp()
                got = self.post(terra(text=bad))
                self.assertFalse(got['queued'])
                self.assertTrue(got['reason'].startswith('model output rejected'), got)
                self.assertEqual(self.outbox(), [])

    def test_abstain_and_no_new_evidence_make_no_post_and_no_second_call(self):
        self.assertEqual(self.post(terra('abstain'))['reason'], 'model abstained')
        call = terra()
        got = self.post(call, now=self.now+700)
        self.assertEqual(got['reason'], 'no new evidence')
        self.assertEqual(call.prompts, [])
        self.assertEqual(self.outbox(), [])

    def test_api_failure_fails_closed_without_text(self):
        got = self.post(terra(error='http_status'))
        self.assertEqual(got['reason'], 'model unavailable; fail closed')
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.record()['intents'], {})
        self.assertEqual(len(self.record()['calls']), 1)

    def test_minimum_interval_counts_old_forum_r3_intents_and_old_record_is_untouched(self):
        self.old['intents']['42'] = {'at':self.now-1800, 'sha256':'a'*64, 'status':'queued'}
        self.old['content'] = ['a'*64]
        self.write_old()
        before = (self.s/'forum-report.json').read_bytes()
        call = terra()
        self.assertEqual(self.post(call)['reason'], 'rate window')
        self.assertEqual(call.prompts, [])
        self.assertEqual(self.post(terra(), now=self.now+1801)['queued'], True)
        self.assertEqual((self.s/'forum-report.json').read_bytes(), before)

    def test_old_reported_ledger_is_never_reposted(self):
        self.old['intents'][str(LEDGER)] = {'at':self.now-90000, 'sha256':'b'*64, 'status':'queued'}
        self.old['content'] = ['b'*64]
        self.write_old()
        self.assertEqual(self.post(terra())['reason'], 'no new evidence')

    def test_rolling_post_cap_and_unknown_hold_never_resent(self):
        t = self.now
        for k in range(6):
            self.pay(LEDGER+1+k, order=f'o-{k+2}')
        for k in range(6):
            led = LEDGER+6-k
            got = self.post(terra(text=text_ok(led).replace('7,000', '7,000')), rows=[settle(seq=led, order=f'o-{7-k}')], now=t)
            self.assertTrue(got['queued'], (k, got))
            t += 3601
        self.assertEqual(self.post(terra(), now=t)['reason'], 'rolling 24h post cap')
        rec = self.record()
        key = str(LEDGER+6)
        rec['intents'][key]['status'] = 'intent'   # simulate a crash between intent and append
        (self.s/forum_terra.RECORD).write_text(json.dumps(rec), encoding='utf-8')
        n = len(self.outbox())
        self.post(terra(), now=t+1)
        self.assertEqual(self.record()['intents'][key]['status'], 'unknown')
        self.assertEqual(len(self.outbox()), n)

    def test_rolling_cap_counts_old_forum_r3_and_terra_intents_together(self):
        for k in range(5):
            h = '%064x' % (k+1)
            self.old['intents'][str(100+k)] = {'at':self.now-7200-k*600, 'sha256':h, 'status':'queued'}
            self.old['content'].append(h)
        self.write_old()
        (self.s/forum_terra.RECORD).write_text(json.dumps({'version':1, 'considered':{}, 'calls':[], 'intents':{
            '99':{'at':self.now-4000, 'status':'verified', 'refs':[99], 'post_sha256':'c'*64}}}), encoding='utf-8')
        call = terra()
        self.assertEqual(self.post(call)['reason'], 'rolling 24h post cap')  # 5 legacy + 1 terra = 6
        self.assertEqual(call.prompts, [])

    def test_malformed_old_forum_r3_record_fails_closed(self):
        self.old['intents']['42'] = {'at':'yesterday', 'sha256':'a'*64, 'status':'queued'}
        self.old['content'] = ['a'*64]
        self.write_old()
        before = (self.s/'forum-report.json').read_bytes()
        call = terra()
        self.assertEqual(self.post(call)['reason'], 'forum_r3 record unreadable')
        self.assertEqual(call.prompts, [])
        self.assertEqual((self.s/'forum-report.json').read_bytes(), before)

    def test_stale_future_or_nonfinite_public_settle_time_fails_closed(self):
        for ts in (self.now-2*86400, self.now+3600, float('nan'), 'now'):
            with self.subTest(ts=ts):
                call = terra()
                row = settle()
                row['ts'] = ts
                got = self.post(call, rows=[row])
                # NaN is already rejected by forum_r3.snapshot (allow_nan=False) before the time check: still no call/post
                self.assertIn(got['reason'], ('public settle time stale, future or invalid', 'no exact public settle')
                              if ts != ts else ('public settle time stale, future or invalid',))
                self.assertEqual(call.prompts, [])
                self.assertEqual(self.outbox(), [])

    def test_returned_model_must_be_exact_terra(self):
        got = self.post(terra(returned='gpt-6.1-terra-2026-01-01'))
        self.assertEqual(got['reason'], 'model unavailable; fail closed')
        self.assertEqual(self.outbox(), [])

    def test_no_exact_public_settle_means_no_call(self):
        call = terra()
        got = self.post(call, rows=[settle(amount=6999)])
        self.assertEqual(got['reason'], 'no exact public settle')
        self.assertEqual(call.prompts, [])

    def test_reconcile_ack_then_exact_public_readback(self):
        self.post(terra())
        line = self.outbox()[0]
        (self.s/'outbox.sent.jsonl').write_text(json.dumps({'status':201, 'sent':line, 'answer':json.dumps({'post':77})})+'\n',
                                                encoding='utf-8')
        wrong = {'id':77, 'author':'someone', 'owner':'kimchyoungman', 'role':'buyer', 'text':line['post'],
                 'refs':[{'seq':LEDGER, 'found':True, 'kind':'settle'}]}
        got = forum_terra.reconcile(self.s, posts=[wrong])
        self.assertEqual((got['acknowledged'], got['verified']), ([77], []))
        good = dict(wrong, author='fair-furniture')
        got = forum_terra.reconcile(self.s, posts=[good])
        self.assertEqual(got['verified'], [77])
        i = self.record()['intents'][str(LEDGER)]
        self.assertEqual((i['status'], i['post_id'], i['readback']['author']), ('verified', 77, 'fair-furniture'))

    def test_inactive_without_gate(self):
        with patch('restock_r3.approved', return_value=False):
            self.assertFalse(forum_terra.active(self.s))
        with patch('restock_r3.approved', return_value=True):
            self.assertTrue(forum_terra.active(self.s))
        (self.s/forum_terra.CONFIG).write_text(json.dumps({'enabled':True, 'max_posts_24h':7}), encoding='utf-8')
        with patch('restock_r3.approved', return_value=True):
            self.assertFalse(forum_terra.active(self.s))

    def test_review_hook_routes_to_terra_only_when_active(self):
        deal = self.s/'deal.json'
        deal.write_text(json.dumps({'settled':{'ledger':LEDGER, 'grade':'A', 'sku':'G1', 'claim_honest':True}}), encoding='utf-8')
        with patch('forum_terra.active', return_value=True), patch('forum_terra.maybe_post') as mp, \
                patch('forum_terra.reconcile') as rc, patch('forum_r3.maybe_report') as old, patch('evidence_memory.sync'):
            out = review_r3.review_file(deal)
        self.assertEqual((mp.call_count, rc.call_count, old.call_count), (1, 1, 0))
        self.assertEqual(out['stars'], 5)
        with patch('forum_terra.active', return_value=False), patch('forum_terra.maybe_post') as mp, \
                patch('forum_r3.enabled', return_value=True), patch('forum_r3.maybe_report') as old, patch('evidence_memory.sync'):
            review_r3.review_file(deal)
        self.assertEqual((mp.call_count, old.call_count), (0, 1))


if __name__ == '__main__':
    unittest.main()
