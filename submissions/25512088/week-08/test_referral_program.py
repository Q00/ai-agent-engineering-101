import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from seller_ledger import Program, policy
from seller_forum import queue_post, sync, SELLER_FORUM_INTERVAL_SECONDS


class ReferralTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = policy()
        self.p['ends_at'] = '2099-01-01T00:00:00+09:00'
        self.p['reward_hold_seconds'] = 100
        self.program = Program(self.tmp.name, self.p)
        self.seq = 0

    def tearDown(self):
        self.program.close()
        self.tmp.cleanup()

    def buy(self, buyer, token='', ref='', base=20000, cost=16000):
        self.seq += 1
        task, unit = f't{self.seq}', f'u{self.seq}'
        q = self.program.quote(base, cost, token, ref)
        secret = self.program.accept(task, unit, 'sku', cost, q)
        row = dict(seq=self.seq, ts=1000, kind='settle', round=3,
                   data=dict(shop='https://ours', buyer=buyer, unit=unit, item='sku', amount=q['price']))
        self.assertTrue(self.program.settle(row, ['https://ours']))
        return secret or token, row, q

    def test_chain_rewards_only_direct_parent_and_replay_is_idempotent(self):
        a, _, _ = self.buy('A')
        b, row, q = self.buy('B', ref=self.program.status(a)['referral_code'])
        self.assertEqual((q['price'], q['reward']), (19400, 400))
        self.assertFalse(self.program.settle(row, ['https://ours']))
        self.program.reconcile(1099, now=2000)
        self.assertEqual(self.program.status(a)['credit'], 0)
        self.program.reconcile(1200, now=1200)
        self.program.reconcile(1200, now=1200)
        self.assertEqual(self.program.status(a)['credit'], 400)
        self.buy('C', ref=self.program.status(b)['referral_code'])
        self.program.reconcile(1200, now=1200)
        self.assertEqual(self.program.status(a)['credit'], 400)
        self.assertEqual(self.program.status(b)['credit'], 400)

    def test_only_paid_own_matching_units_activate(self):
        q = self.program.quote(10000, 8000)
        secret = self.program.accept('t', 'u', 'sku', 8000, q)
        self.assertFalse(self.program.status(secret)['active'])
        for shop, amount in [('https://fake', 10000), ('https://ours', 1)]:
            row = dict(seq=100, ts=1000, kind='settle', round=3,
                       data=dict(shop=shop, buyer='A', unit='u', item='sku', amount=amount))
            self.assertFalse(self.program.settle(row, ['https://ours']))
        self.assertFalse(self.program.status(secret)['active'])

    def test_warranty_acceptance_uses_fresh_official_hazard(self):
        now=time.time()
        with self.program.db:
            for n in range(20):
                self.program.db.execute(
                    'INSERT INTO sales(task,unit,sku,amount,cost,settled) VALUES(?,?,?,?,?,?)',
                    (f'old-{n}',f'old-unit-{n}','old',1000000,800000,now))
                self.program.db.execute('INSERT INTO warranties VALUES(?,?,?)',
                                        (f'old-{n}',1000000,now))
        Path(self.tmp.name,'market-report.json').write_text(json.dumps({
            'at':now,'board':{'shops':[{'name':'25512088','claims':1520,'broken':37}]}}))
        q=self.program.quote(100000,80000);q['warranty_hours']=6
        self.assertIsNone(self.program.accept('fresh','fresh-unit','fresh',80000,q,
                                               inventory={'balance':6000000,'bankrupt_line':500000},
                                               enroll=False))

    def test_founder_needs_paid_review_and_own_forum_not_stars(self):
        token, row, _ = self.buy('A')
        result = dict(kind='result', data=dict(shop='https://ours', buyer='A', verified=True,
                      checks={'settled': True}, stars=1, review='가격이 아쉬웠다', receipt={'task': 't1'}))
        self.program.review(result, ['https://ours'])
        self.program.forum([dict(role='buyer', author='impostor', refs=[row['seq']], text='후기')])
        self.program.reconcile(1200, now=1200)
        self.assertFalse(self.program.status(token)['founder'])
        self.program.forum([dict(role='buyer', author='A', refs=[{'seq': row['seq'], 'found': True, 'kind': 'settle'}], text='후기 참여 혜택을 받습니다')])
        self.program.reconcile(1200, now=1200)
        self.assertTrue(self.program.status(token)['founder'])
        q = self.program.quote(10000, 9900, token)
        self.assertEqual(q['price'], 9500)  # Fixed promise even on a low-margin unit.

    def test_duplicate_buyer_cannot_earn_referral_or_duplicate_badge(self):
        a, _, _ = self.buy('A')
        alias, _, _ = self.buy('A', ref=self.program.status(a)['referral_code'])
        self.program.reconcile(1200, now=1200)
        self.assertFalse(self.program.status(alias)['active'])
        self.assertEqual(self.program.status(a)['credit'], 0)

    def test_refund_cancels_or_reverses_reward_once(self):
        a, _, _ = self.buy('A')
        _, row, _ = self.buy('B', ref=self.program.status(a)['referral_code'])
        self.program.reconcile(1200, now=1200)
        self.program.refund_unit(row['data']['unit'])
        self.program.refund_unit(row['data']['unit'])
        self.assertEqual(self.program.status(a)['credit'], 0)

    def test_reservations_survive_restart_and_prevent_double_spend(self):
        a, _, _ = self.buy('A')
        self.buy('B', ref=self.program.status(a)['referral_code'])
        self.program.reconcile(1200, now=1200)
        q = self.program.quote(20000, 16000, a)
        self.program.accept('pending', 'pending-unit', 'sku', 16000, q)
        self.program.close()
        self.program = Program(self.tmp.name, self.p)
        self.assertEqual(self.program.quote(20000, 16000, a)['spent'], 0)
        with self.assertRaises(ValueError):
            self.program.accept('other', 'other-unit', 'sku', 16000, q)
        self.assertIn('pending-unit', self.program.reserved_units())

    def test_signed_reservation_expires_after_thirty_seconds_and_unit_reopens(self):
        q=self.program.quote(10000,8000)
        self.program.accept('old-task','shared-unit','sku',8000,q,enroll=False)
        signed=self.program.db.execute("SELECT ts FROM growth_events WHERE task='old-task' AND event='signed'").fetchone()[0]
        self.assertIn('shared-unit',self.program.reserved_units(now=signed+29.999))
        self.assertNotIn('shared-unit',self.program.reserved_units(now=signed+30.001))
        self.assertEqual(self.program.db.execute("SELECT reason FROM expired_sales WHERE task='old-task'").fetchone()[0],
                         'checkout_lease_expired')
        self.program.accept('new-task','shared-unit','sku',8000,q,enroll=False)
        self.assertIn('shared-unit',self.program.reserved_units())

    def test_late_official_settlement_restores_expired_receipt(self):
        q=self.program.quote(10000,8000)
        self.program.accept('old-task','shared-unit','sku',8000,q,enroll=False)
        signed=self.program.db.execute("SELECT ts FROM growth_events WHERE task='old-task' AND event='signed'").fetchone()[0]
        self.program.expire_stale(signed+31)
        row=dict(seq=998,ts=signed+32,kind='settle',round=3,
                 data=dict(shop='https://ours',buyer='late',unit='shared-unit',item='sku',amount=10000))
        self.assertTrue(self.program.settle(row,['https://ours']))
        self.assertEqual(self.program.db.execute("SELECT ledger FROM sales WHERE task='old-task'").fetchone()[0],998)
        self.assertEqual(self.program.db.execute("SELECT COUNT(*) FROM expired_sales WHERE task='old-task'").fetchone()[0],0)

    def test_unaccepted_quote_holds_never_hide_inventory(self):
        with self.program.db:
            self.program.db.execute('CREATE TABLE IF NOT EXISTS quote_holds(task TEXT PRIMARY KEY, unit TEXT, until REAL)')
            self.program.db.execute("INSERT INTO quote_holds VALUES('quote-only','free-unit',9999999999)")
        self.assertNotIn('free-unit',self.program.reserved_units())

    def test_official_failed_result_releases_signed_inventory_and_is_idempotent(self):
        q=self.program.quote(10000,8000)
        self.program.accept('failed-task','failed-unit','sku',8000,q,enroll=False)
        with self.program.db:
            self.program.db.execute("INSERT INTO warranties VALUES('failed-task',10000,1000)")
        row=dict(seq=500,ts=1100,kind='result',round=3,data=dict(
            shop='https://ours',verified=False,checks={'settled':False},
            receipt=dict(task='failed-task',unit='failed-unit',item='sku',price=10000)))
        self.assertTrue(self.program.release_failed(row,['https://ours']))
        self.assertNotIn('failed-unit',self.program.reserved_units())
        self.assertEqual(self.program.db.execute('SELECT COUNT(*) FROM failed_sales').fetchone()[0],1)
        self.assertEqual(self.program.db.execute('SELECT COUNT(*) FROM warranties WHERE task=?',('failed-task',)).fetchone()[0],0)
        self.assertFalse(self.program.release_failed(row,['https://ours']))

    def test_unconfirmed_or_mismatched_result_never_releases_inventory(self):
        q=self.program.quote(10000,8000)
        self.program.accept('pending','pending-unit','sku',8000,q,enroll=False)
        base=dict(seq=501,ts=1100,kind='result',round=3,data=dict(
            shop='https://ours',verified=False,checks={'settled':True},
            receipt=dict(task='pending',unit='pending-unit',item='sku',price=10000)))
        self.assertFalse(self.program.release_failed(base,['https://ours']))
        base['data']['checks']['settled']=False;base['data']['receipt']['price']=9999
        self.assertFalse(self.program.release_failed(base,['https://ours']))
        self.assertIn('pending-unit',self.program.reserved_units())

    def test_public_referral_code_cannot_unlock_private_membership(self):
        a, _, _ = self.buy('A')
        code = self.program.status(a)['referral_code']
        self.assertFalse(self.program.status(code)['active'])

    def test_sunset_sale_records_without_creating_member(self):
        q=self.program.quote(10000,8000)
        self.assertIsNone(self.program.accept('sunset','sunset-unit','sku',8000,q,enroll=False))
        self.assertEqual(self.program.db.execute('SELECT COUNT(*) FROM members').fetchone()[0],0)
        row=dict(seq=999,ts=1000,kind='settle',round=3,
                 data=dict(shop='https://ours',buyer='new',unit='sunset-unit',item='sku',amount=10000))
        self.assertTrue(self.program.settle(row,['https://ours']))

    def test_small_margin_caps_acquisition_and_no_discount_after_end(self):
        a, _, _ = self.buy('A')
        q = self.program.quote(10000, 9900, ref=self.program.status(a)['referral_code'])
        self.assertLessEqual(10000-q['price']+q['reward'], 25)
        self.program.p['ends_at'] = '2000-01-01T00:00:00+09:00'
        self.assertEqual(self.program.quote(10000, 8000, ref=self.program.status(a)['referral_code'])['price'],10000)

    def test_forum_queue_has_limit_and_never_posts_as_buyer(self):
        inv = {'round': 3, 'units': [{'name': 'chair', 'grade': 'A', 'kind': 'goods'}]}
        self.assertTrue(queue_post(self.program, self.tmp.name, inv, now=10000))
        self.assertFalse(queue_post(self.program, self.tmp.name, inv, now=10001))
        rows = (Path(self.tmp.name)/'outbox.jsonl').read_text().splitlines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0])['as'], 'shop')

    def test_seller_forum_uses_four_hour_c_liquidation_campaign(self):
        inv={'round':3,'units':[
            {'unit':'a','sku':'GA','name':'normal','grade':'A','kind':'goods','cost':6000,'list_price':10000},
            {'unit':'c','sku':'GC','name':'clearance','grade':'C','kind':'goods','cost':3000,'list_price':10000},
        ]}
        self.assertTrue(queue_post(self.program,self.tmp.name,inv,now=20000))
        self.assertFalse(queue_post(self.program,self.tmp.name,inv,now=20000+SELLER_FORUM_INTERVAL_SECONDS-1))
        post=json.loads((Path(self.tmp.name)/'outbox.jsonl').read_text().splitlines()[0])['post']
        self.assertIn('C등급 실재고 할인',post)
        self.assertLess(post.index('GC clearance'),post.index('GA normal'))
        self.assertIn('정가 대비',post)

    def test_seller_forum_excludes_food_with_under_six_hours_left(self):
        inv={'round':3,'units':[
            {'unit':'soon','sku':'SOON','name':'soon','grade':'C','kind':'food','cost':3000,
             'list_price':10000,'expires_at':20000+5*3600},
            {'unit':'goods','sku':'GOODS','name':'goods','grade':'C','kind':'goods','cost':3000,
             'list_price':10000},
        ]}
        self.assertTrue(queue_post(self.program,self.tmp.name,inv,now=20000))
        post=json.loads((Path(self.tmp.name)/'outbox.jsonl').read_text().splitlines()[0])['post']
        self.assertIn('GOODS goods',post)
        self.assertNotIn('SOON soon',post)

    def test_worker_replays_after_crash_without_double_award(self):
        self.program.put('cursor', 0)
        a, _, _ = self.buy('A')
        _, row, _ = self.buy('B', ref=self.program.status(a)['referral_code'])
        with patch('seller_forum.fetch', side_effect=[[row], []]), patch('seller_forum.time.time', return_value=1200):
            self.assertTrue(sync(self.program, ['https://ours']))
        self.assertEqual(self.program.status(a)['credit'], 400)

    def test_worker_consumes_official_failed_result(self):
        self.program.put('cursor',0)
        q=self.program.quote(10000,8000)
        self.program.accept('failed','failed-unit','sku',8000,q,enroll=False)
        row=dict(seq=2,ts=1000,kind='result',round=3,data=dict(
            shop='https://ours',verified=False,checks={'settled':False},
            receipt=dict(task='failed',unit='failed-unit',item='sku',price=10000)))
        with patch('seller_forum.fetch',side_effect=[[row],[]]),patch('seller_forum.time.time',return_value=1200):
            self.assertTrue(sync(self.program,['https://ours']))
        self.assertNotIn('failed-unit',self.program.reserved_units())
        self.assertEqual(self.program.db.execute('SELECT COUNT(*) FROM failed_sales').fetchone()[0],1)


if __name__ == '__main__':
    unittest.main()
