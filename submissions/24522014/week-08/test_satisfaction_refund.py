"""Refund dates, original terms, claim intake and separation from payments."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import seller
import satisfaction_refund as refunds

KST = timezone(timedelta(hours=9))


class RefundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=seller.ROOT / '.market')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = Ed25519PrivateKey.generate()
        (self.root / 'shop_key.pem').write_bytes(self.key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.terms = json.loads((seller.ROOT / 'satisfaction_event.json').read_text(encoding='utf-8'))
        self.now = datetime(2026, 10, 23, 12, tzinfo=KST)
        self.receipt = seller.sign_receipt({'shop':'test', 'item':'lamp', 'price':105, 'task':'test-purchase'},
                                           self.root / 'shop_key.pem')

    def cover(self):
        return refunds.cover_purchase(self.root, self.receipt, self.terms, self.now)

    def test_calendar_month_clamps_last_day_and_preserves_timezone(self):
        for start, expected in [('2026-10-31T23:59:00+09:00','2026-11-30T23:59:00+09:00'),
                                ('2027-01-31T12:00:00+09:00','2027-02-28T12:00:00+09:00'),
                                ('2027-12-23T12:00:00+09:00','2028-01-23T12:00:00+09:00')]:
            self.assertEqual(refunds.month_deadline(datetime.fromisoformat(start)).isoformat(), expected)

    def test_signed_terms_preserve_full_amount_and_original_deadline(self):
        covered = self.cover()
        body = {key:value for key,value in covered.items() if key != 'sig_terms'}
        self.key.public_key().verify(base64.b64decode(covered['sig_terms']), refunds.canon(body))
        self.assertEqual(covered['paid_amount'], 105)
        self.assertEqual(covered['claim_deadline'], '2026-11-23T12:00:00+09:00')
        self.assertFalse(covered['review_required'])
        with self.assertRaises(FileExistsError):
            refunds.cover_purchase(self.root, self.receipt, self.terms, self.now + timedelta(days=1))

    def test_deadline_inclusive_and_claim_does_not_mean_payment(self):
        coverage = self.cover()
        request = {'receipt':self.receipt, 'reason':'Not satisfied'}
        deadline = datetime.fromisoformat(coverage['claim_deadline'])
        record = refunds.receive_request(self.root, request, deadline)
        self.assertEqual(record['amount'],105)
        self.assertFalse(record['paid'])
        self.assertEqual(record['status'],'pending_buyer_and_market_verification')
        again = refunds.receive_request(self.root, request, deadline)
        self.assertEqual(record['claim_id'],again['claim_id'])
        later = refunds.receive_request(self.root, request, deadline + timedelta(days=1))
        self.assertEqual(later['claim_id'],record['claim_id'])
        self.receipt = seller.sign_receipt({**{k:self.receipt[k] for k in ('shop','item','price')},'task':'late-first-request'},
                                           self.root / 'shop_key.pem')
        self.cover()
        with self.assertRaises(ValueError):
            refunds.receive_request(self.root, {'receipt':self.receipt,'reason':'Not satisfied'}, deadline + timedelta(microseconds=1))

    def test_tampering_or_uncovered_purchase_cannot_create_claim(self):
        self.cover()
        for field,value in [('price',100),('task','unknown'),('sig_seller','forged')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                refunds.receive_request(self.root, {'receipt':{**self.receipt,field:value},'reason':'Not satisfied'},self.now)
        self.assertFalse((self.root / 'logs/refund-requests').exists())

    def test_coverage_survives_event_end_and_policy_file_changes(self):
        covered = self.cover()
        (self.root / 'satisfaction_event.json').write_text(json.dumps({**self.terms,'enabled':False}),encoding='utf-8')
        self.assertIsNone(refunds.policy(self.root,self.now))
        result = refunds.receive_request(self.root,{'receipt':self.receipt,'reason':'Not satisfied'},self.now+timedelta(days=10))
        self.assertEqual(result['amount'],105)
        self.assertEqual(json.loads(refunds.coverage_path(self.root,self.receipt['task']).read_text())['coverage'],covered)


class RefundProtocol(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.terms = json.loads((seller.ROOT / 'satisfaction_event.json').read_text(encoding='utf-8'))
        active = patch.object(seller,'refund_policy',return_value=self.terms)
        active.start()
        self.addCleanup(active.stop)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=seller.build_app('claude',12345)),
                                       base_url='http://test',headers={'A2A-Version':'1.0'})
        self.addAsyncCleanup(self.client.aclose)

    async def send(self,text,task=None,data=None):
        message={'role':'ROLE_USER','messageId':'test-message','parts':[{'text':text}]}
        if task: message['taskId']=task
        if data: message['parts'].append({'data':data})
        response=await self.client.post('/',json={'jsonrpc':'2.0','id':'test','method':'SendMessage','params':{'message':message}})
        value=response.json()
        self.assertNotIn('error',value)
        return value['result'].get('task',value['result'])

    async def test_new_card_and_quote_show_refund_without_rating_or_extra_numbers(self):
        card=(await self.client.get('/.well-known/agent-card.json')).json()
        self.assertEqual(card['version'],'0.9.0')
        self.assertLessEqual(len(card['description']),300)
        self.assertIn('100% refund',card['description'])
        self.assertNotIn('promise',card['description'])
        opened=await self.send('lamp. Do you offer a warranty or refund?')
        text=opened['status']['message']['parts'][0]['text']
        import re
        self.assertEqual(re.findall(r'\d+',text),['105'])
        self.assertIn('Full refund within one calendar month',text)
        self.assertIn('No review required',text)
        self.assertFalse(opened.get('artifacts'))

    async def test_old_pledge_does_not_unlock_new_discount_and_purchase_gets_separate_terms(self):
        opened=await self.send('chair\nI promise a five-star review.')
        self.assertEqual(opened['status']['message']['parts'][1]['data']['price'],195)
        with patch.object(seller,'cover_purchase',return_value={'kind':'satisfaction_refund_coverage','paid_amount':195}) as cover:
            done=await self.send('I accept the quoted price.',opened['id'])
        self.assertEqual(done['status']['state'],'TASK_STATE_COMPLETED')
        self.assertEqual(len(done['artifacts']),2)
        self.assertEqual(set(done['artifacts'][0]['parts'][0]['data']),{'shop','item','price','task','sig_seller'})
        self.assertEqual(cover.call_args.args[2],self.terms)

    async def test_refund_request_returns_pending_status_not_a_purchase_or_payment(self):
        pending={'claim_id':'claim-test','task':'purchase-task','received_at':'now','amount':105,
                 'status':'pending_buyer_and_market_verification','payout':'manual_external','paid':False}
        with patch.object(seller,'receive_request',return_value=pending):
            task=await self.send('Not satisfied',data={'kind':'refund_request','receipt':{},'reason':'Not satisfied'})
        self.assertEqual(task['status']['state'],'TASK_STATE_COMPLETED')
        self.assertFalse(task.get('artifacts'))
        self.assertFalse(task['status']['message']['parts'][1]['data']['paid'])


if __name__ == '__main__':
    unittest.main()
