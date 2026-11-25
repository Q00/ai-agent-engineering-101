import asyncio,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import seller_featured as featured
import seller_forum as forum
import seller_r3
import test_seller_persistence as fixtures
from seller_state import SellerState
from market_memory import events


class FeaturedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.now=time.time()
        self.unit={'unit':'u','sku':'g','name':'Goods','grade':'B','kind':'goods','cost':500,'list_price':1000,'supplied_at':self.now-86400,'expires_at':None}
        self.offer=featured.proposals([self.unit],self.root,self.now)[0];self.db=self.root/'seller.sqlite3'
    def test_prices_cover_holding_and_refund_scenario_and_show_real_reference_saving(self):
        self.assertGreater(self.offer['price'],featured.floor(self.unit,self.now))
        self.assertLessEqual(self.offer['price'],900)
        self.assertEqual(featured.validate([self.offer['id']],[self.offer],[self.unit],self.root,self.now),[self.offer])
    def test_bounded_proposals_do_not_let_one_sku_hide_all_other_stock(self):
        units=[{**self.unit,'unit':f'u{i}'} for i in range(30)]+[{**self.unit,'unit':'other','sku':'other'}]
        choices=featured.proposals(units,self.root,self.now)
        self.assertEqual({o['sku'] for o in choices},{'g','other'})
        self.assertEqual(len({o['unit'] for o in choices if o['sku']=='g'}),1)

    def test_expiry_grade_change_lease_or_unavailable_unit_prevents_advertising(self):
        for units,now in (([],self.now),([{**self.unit,'grade':'C'}],self.now),([self.unit],self.offer['valid_until'])):
            with self.assertRaises(ValueError):featured.validate([self.offer['id']],[self.offer],units,self.root,now)
    def test_max_three_active_and_failed_publication_does_not_keep_honoring(self):
        featured.activate(self.db,[self.offer],'post1',self.now)
        self.assertEqual(len(featured.active(self.db,self.now)),1)
        for i in range(2):
            o={**self.offer,'id':f'FO-000000000{i}','unit':f'other{i}'};featured.activate(self.db,[o],f'post{i+2}',self.now)
        with self.assertRaises(ValueError):featured.activate(self.db,[{**self.offer,'id':'FO-aaaaaaaaaa','unit':'fourth'}],'post4',self.now)
        featured.acknowledge(self.db,'post1','failed')
        self.assertEqual(len(featured.active(self.db,self.now)),2)
    def test_existing_offer_is_not_extended_by_reactivation_and_restart(self):
        featured.activate(self.db,[self.offer],'post1',self.now)
        featured.activate(self.db,[{**self.offer,'valid_until':self.now+9000}],'post1',self.now+1)
        self.assertEqual(featured.active(self.db,self.now+1)[0]['valid_until'],self.offer['valid_until'])
        self.assertEqual(featured.active(self.db,self.offer['valid_until']),[])
    def test_only_selected_physical_unit_gets_the_price_ceiling_and_lower_quote_is_kept(self):
        q=featured.apply(self.unit,900,[self.offer],self.now)
        self.assertEqual(q['price'],self.offer['price'])
        self.assertEqual(featured.apply(self.unit,600,[self.offer],self.now)['price'],max(600,q['price_floor']))
        self.assertIsNone(featured.apply({**self.unit,'unit':'other'},900,[self.offer],self.now))
        self.assertIsNone(featured.apply(self.unit,900,[self.offer],self.offer['valid_until']))
    def test_public_savings_round_down_and_editor_cannot_invent_the_price(self):
        decision={'campaign':'featured','headline':'실제 조건 공개','body':'조건을 확인하고 가격을 제안하세요.','skus':['g'],'offer_ids':[self.offer['id']],'ledger_refs':[],'thread_id':None}
        evidence={'at':self.now,'opening_event':None,'stock':[{'sku':'g','name':'Goods','units':[self.unit]}],'featured_quotes':[self.offer]}
        text=forum.render(decision,evidence)
        self.assertIn(f"최대 {self.offer['price']:,}원",text);self.assertIn(self.offer['id'],text)
        self.assertNotIn('원가',text)


class FeaturedPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.now=time.time()
        for folder in ('.market','state','logs'):(self.root/folder).mkdir()
        self.db=self.root/'state/seller.sqlite3'
        with sqlite3.connect(self.db) as db:db.execute('CREATE TABLE leases (unit TEXT,until REAL)')
        unit={'unit':'u','sku':'g','name':'스툴','category':'가구>소파/의자/스툴','grade':'B',
              'kind':'goods','cost':500,'list_price':1000,'supplied_at':self.now-100,'expires_at':None}
        (self.root/'.market/inventory.json').write_text(json.dumps({'round':3,'units':[unit]}))
        import os
        os.utime(self.root/'.market/inventory.json',(self.now,self.now))
        (self.root/'.market/forum.json').write_text('[]')
    def model(self,role,prompt,schema):
        if 'checker' in role:
            self.assertEqual(len(prompt['facts']['featured_quotes']),1)
            self.assertIn(prompt['facts']['featured_quotes'][0]['id'],prompt['rendered_post'])
            return {'approve':True,'unsupported_claims':[]}
        choices=prompt['facts']['featured_quotes']
        self.offer=choices[0]
        return {'action':'post','campaign':'featured','skus':['g'],'offer_ids':[self.offer['id']],
                'ledger_refs':[],'thread_id':None,'headline':'가격 조건을 미리 확인하세요',
                'body':'필요한 등급을 확인하고 상담해 주세요.','reason':'Test tool-backed public price'}
    def test_queue_ack_and_cap_use_same_pinned_price_and_deadline(self):
        forum.run_once(self.root,now=self.now,decide=self.model)
        active=featured.active(self.db,self.now)
        self.assertEqual(active[0]['price'],self.offer['price']);self.assertEqual(active[0]['valid_until'],self.offer['valid_until'])
        payload=json.loads((self.root/'.market/outbox.jsonl').read_text())
        (self.root/'.market/outbox.sent.jsonl').write_text(json.dumps({'sent':payload,'status':200,'answer':'{"post":555}'})+'\n')
        forum.run_once(self.root,now=self.now+1,decide=lambda *a:self.fail('cooldown'))
        with sqlite3.connect(self.db) as db:self.assertEqual(db.execute('SELECT phase FROM featured_quotes').fetchone()[0],'published')
    def test_dry_run_never_activates_a_quote_or_hands_off(self):
        forum.run_once(self.root,now=self.now,decide=self.model,dry_run=True)
        self.assertEqual(featured.active(self.db,self.now),[])
        self.assertFalse((self.root/'.market/outbox.jsonl').exists())
    def test_checker_rejection_prevents_both_binding_offer_and_publication(self):
        def model(role,prompt,schema):
            if 'checker' in role:return {'approve':False,'unsupported_claims':['Test reject']}
            return self.model(role,prompt,schema)
        with self.assertRaisesRegex(ValueError,'copy rejected'):forum.run_once(self.root,now=self.now,decide=model)
        self.assertEqual(featured.active(self.db,self.now),[])
        self.assertFalse((self.root/'.market/outbox.jsonl').exists())

    def write_requests(self, budget=800):
        requests = [
            {'id': 41, 'role': 'buyer', 'ts': self.now, 'text': f'스툴 구합니다. B급 예산 {budget}원.'},
            {'id': 42, 'role': 'buyer', 'ts': self.now, 'text': '스툴 구합니다. B급 예산 100원.'},
        ]
        (self.root/'.market/forum.json').write_text(json.dumps(requests, ensure_ascii=False), encoding='utf-8')

    def quote_reply(self, role, prompt, schema):
        if 'checker' in role:
            return self.model(role, prompt, schema)
        quotes = prompt['facts']['reply_quotes']
        self.assertEqual(list(quotes), [41])
        self.offer = quotes[41][0]
        return {'action': 'post', 'campaign': 'reply', 'thread_id': 41, 'skus': ['g'],
                'offer_ids': [self.offer['id']], 'ledger_refs': [], 'headline': '찾으시는 스툴 안내',
                'body': '요청하신 조건으로 상담하실 수 있습니다.', 'reason': 'Use the checked reply quote.'}

    def test_request_reply_binds_code_to_original_thread_and_unit_through_ack(self):
        self.write_requests()
        forum.run_once(self.root, now=self.now, decide=self.quote_reply)
        payload = json.loads((self.root/'.market/outbox.jsonl').read_text())
        self.assertEqual(payload['thread'], 41)
        self.assertIn(self.offer['id'], payload['post'])
        self.assertIn(f"최대 {self.offer['price']:,}원", payload['post'])
        self.assertEqual(featured.active(self.db,self.now)[0]['source_thread'], 41)
        (self.root/'.market/outbox.sent.jsonl').write_text(json.dumps({'sent':payload,'status':200,'answer':'{"post":555}'})+'\n')
        forum.run_once(self.root, now=self.now+1, decide=lambda *a:self.fail('cooldown'))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT phase FROM featured_quotes').fetchone()[0], 'published')

    def test_budget_change_after_checker_prevents_quote_and_publication(self):
        self.write_requests()

        def decide(role, prompt, schema):
            result = self.quote_reply(role, prompt, schema)
            if 'checker' in role:
                self.write_requests(budget=100)
            return result

        with self.assertRaisesRegex(ValueError, 'no longer meets'):
            forum.run_once(self.root, now=self.now, decide=decide)
        self.assertEqual(featured.active(self.db,self.now), [])
        self.assertFalse((self.root/'.market/outbox.jsonl').exists())


class FeaturedContractTests(unittest.TestCase):
    tearDown=fixtures.PersistenceTests.tearDown
    write_inventory=fixtures.PersistenceTests.write_inventory
    send=fixtures.PersistenceTests.send
    client=fixtures.PersistenceTests.client
    def setUp(self):
        fixtures.PersistenceTests.setUp(self)
        self.unit.update(kind='goods',grade='B',expires_at=None,name='Example Goods');self.write_inventory()
        self.offer=featured.proposals([self.unit],Path('.market'),self.now)[0]
        featured.activate(Path('state/seller.sqlite3'),[self.offer],'public-post',self.now)
    def test_real_a2a_quote_ceiling_and_receipt_survive_server_rebuild_and_offer_expiry(self):
        async def flow():
            async with self.client() as c:first=await self.send(c,self.offer['id'])
            self.assertEqual(first['status']['state'],'TASK_STATE_INPUT_REQUIRED')
            deal=SellerState().restore_deals()[first['id']]
            self.assertEqual(deal['ask'],self.offer['price'])
            with sqlite3.connect('state/seller.sqlite3') as db:db.execute('UPDATE featured_quotes SET valid_until=0')
            async with self.client() as c:final=await self.send(c,str(self.offer['price']),first['id'])
            self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
            receipt=SellerState().pending_receipt(first['id']);self.assertEqual(receipt['price'],self.offer['price']);self.assertEqual(receipt['warranty_hours'],6)
            self.assertEqual(next(e for e in events() if e['event']=='receipt_signed')['public_offer_id'],self.offer['id'])
        answers=[{'action':'ask','price':900,'unit_id':'u1','reason':'ordinary quote'},{'action':'accept','price':None,'unit_id':'u1','reason':'agree'}]
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())
    def test_code_expiring_during_model_call_never_falls_back_to_an_ordinary_price(self):
        apply=featured.apply
        async def flow():
            async with self.client() as c:r=await self.send(c,self.offer['id'])
            self.assertEqual(r['status']['state'],'TASK_STATE_REJECTED')
            self.assertEqual(SellerState().restore_deals(),{})
        with patch.object(seller_r3,'decide_json',return_value={'action':'ask','price':900,'unit_id':'u1','reason':'ordinary'}), \
             patch.object(featured,'apply',side_effect=lambda u,p,offers,now:apply(u,p,offers,self.offer['valid_until']+1)):
            asyncio.run(flow())

    def test_unknown_or_expired_code_is_not_silently_replaced_with_a_more_expensive_unit(self):
        async def flow():
            async with self.client() as c:r=await self.send(c,'FO-0000000000')
            self.assertEqual(r['status']['state'],'TASK_STATE_REJECTED')
        with patch.object(seller_r3,'decide_json',side_effect=AssertionError('invalid code')):asyncio.run(flow())

if __name__=='__main__':unittest.main()


class MidpriceVisibilityTests(unittest.TestCase):
    def test_middle_ab_unit_remains_visible_among_many_cheap_and_c_units(self):
        now=time.time()
        base={'name':'Goods','grade':'B','kind':'goods','supplied_at':now,
              'expires_at':None,'cost':1000,'list_price':3000}
        low=[{**base,'sku':f'cheap{i}','unit':f'low{i}'} for i in range(30)]
        middle={**base,'sku':'middle','unit':'good-mid','cost':120000,'list_price':270000}
        c={**middle,'unit':'c-mid','grade':'C','cost':30000}
        with tempfile.TemporaryDirectory() as tmp:
            choices=featured.proposals(low+[middle,c],Path(tmp),now)
            mid=[o for o in choices if o['sku']=='middle']
            self.assertTrue(mid)
            self.assertEqual({o['unit'] for o in mid},{'good-mid'})
            self.assertTrue(any(o['margin_scenario']==.02 for o in mid))
            self.assertTrue(all(o['price']>featured.floor(middle,now) for o in mid))
            self.assertLessEqual(len(choices),24)
            self.assertTrue(any(o['sku'].startswith('cheap') for o in choices))
