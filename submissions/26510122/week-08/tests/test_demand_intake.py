import json,os,random,sqlite3,tempfile,unittest
from pathlib import Path
import seller_demand as demand
import seller_forum as forum
import inventory_portfolio as portfolio
from test_inventory_portfolio import catalog as fixture_catalog,inventory as fixture_inventory


class DemandIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.now=1000
        for folder in ('.market','state','logs'):(self.root/folder).mkdir()
        with sqlite3.connect(self.root/'state/seller.sqlite3') as db:db.execute('CREATE TABLE leases (unit TEXT,until REAL)')
        self.cat={'s1':{'name':'디카페인 커피 라떼','category':'식품>음료>커피/차','kind':'food','list_price':10000,'shelf_hours':600}}
        (self.root/'.market/catalog.json').write_text(json.dumps(self.cat))
        self.write_inventory([],self.now)
    def write_inventory(self,units,now):
        p=self.root/'.market/inventory.json';p.write_text(json.dumps({'round':3,'balance':5000000,'units':units}));os.utime(p,(now,now))
    def feed(self,rows,now=None):
        p=self.root/'.market/forum.json';p.write_text(json.dumps(rows));at=self.now if now is None else now;os.utime(p,(at,at))
    def request(self,**kw):
        return {'id':77,'role':'buyer','author':'customer','owner':'customer-owner','round':3,'ts':self.now,'text':'커피/차 구합니다. B등급, 예산은 9만 원대이며 360시간 안에 먹을 예정입니다.',**kw}
    def test_unstocked_request_is_durable_and_not_an_order_or_sale(self):
        self.feed([self.request()]);facts=forum.facts(self.root,self.now)
        self.assertEqual(facts['requests'],[]);self.assertEqual(facts['sourcing_requests'][0]['id'],77)
        demand.ingest(self.root,self.cat,self.now)
        with sqlite3.connect(self.root/'state/seller.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forum_demand').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM forum_demand_versions').fetchone()[0],1)
        ctx=demand.candidate_context(self.root,self.now)['s1']
        self.assertEqual(ctx['request_ids'],[77]);self.assertEqual(ctx['examples'][0]['requirements']['budget_max'],99999)
        self.assertIn('Not an order',ctx['caution'])
    def test_request_persists_outside_feed_and_reply_waits_for_real_conditions(self):
        self.feed([self.request()]);forum.facts(self.root,self.now)
        later=1100;self.feed([],later)
        unit={'unit':'arrived','sku':'s1','name':self.cat['s1']['name'],'category':self.cat['s1']['category'],
              'kind':'food','cost':5000,'list_price':10000,'grade':'C','expires_at':later+400*3600,'supplied_at':later}
        self.write_inventory([unit],later);self.assertEqual(forum.facts(self.root,later)['requests'],[])
        unit['grade']='B';self.write_inventory([unit],later)
        self.assertEqual(forum.facts(self.root,later)['requests'][0]['id'],77)
        decision={'action':'post','campaign':'reply','skus':['s1'],'offer_ids':[],'ledger_refs':[],
                  'thread_id':77,'headline':'찾으신 커피 재고 안내','body':'요청하신 조건을 확인해 안내합니다.','reason':'matching physical arrival'}
        def model(role, prompt, schema):
            if 'checker' in role:
                return {'approve': True, 'unsupported_claims': []}
            self.assertEqual(schema['properties']['campaign']['enum'], ['reply'])
            quote = prompt['facts']['reply_quotes'][77][0]
            self.assertEqual(quote['unit'], 'arrived')
            return decision | {'offer_ids': [quote['id']]}
        forum.run_once(self.root, now=later, decide=model)
        payload = json.loads((self.root/'.market/outbox.jsonl').read_text())
        self.assertEqual(payload['thread'], 77)
        self.assertIn('B등급', payload['post'])
        self.assertIn('400시간', payload['post'])
        self.assertIn('견적코드 FO-', payload['post'])
    def test_survey_reply_without_buy_verb_is_captured(self):
        forum.save(self.root/'state/seller-forum.json',{'posts':[{'campaign':'demand_survey','status':'published','post_id':12,'at':900}]})
        self.feed([self.request(thread=12,text='커피/차 · B급 · 예산 4만원 · 3일 안에 먹어요')])
        demand.ingest(self.root,self.cat,self.now);row=demand.active(self.root,self.now)[0]
        self.assertTrue(row['from_intake']);self.assertEqual(row['requirements']['min_hours_left'],72)
        self.assertEqual(row['requirements']['budget_max'],40000);self.assertEqual(row['requirements']['grades'],['B'])
    def test_closed_request_revisions_remain_in_raw_history_but_stop_sourcing(self):
        self.feed([self.request()]);demand.ingest(self.root,self.cat,self.now)
        self.feed([self.request(text='커피/차 구했습니다. 구매 완료')],1001);demand.ingest(self.root,self.cat,1001)
        self.assertEqual(demand.active(self.root,1001),[])
        with sqlite3.connect(self.root/'state/seller.sqlite3') as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM forum_demand_versions').fetchone()[0],2)
    def test_generic_tip_and_own_posts_are_not_customer_intents(self):
        self.feed([self.request(text='커피 고르는 법과 확인 포인트를 추천합니다'),self.request(id=78,author=forum.SHOP)])
        demand.ingest(self.root,self.cat,self.now);self.assertEqual(demand.active(self.root,self.now),[])
    def test_cover_is_not_matched_as_a_mattress_and_pet_seating_is_not_human_seating(self):
        cat={'bed':{'name':'Example mattress','category':'가구>침대/매트리스'},'cover':{'name':'Example cover','category':'침구>매트리스커버'},'pet':{'name':'반려견 스텝퍼','category':'가구>소파/의자/스툴'}}
        self.assertEqual([m['sku'] for m in demand.matches('매트리스커버 구합니다',cat)],['cover'])
        self.assertEqual(demand.matches('소파 구합니다',{'pet':cat['pet']}),[])
    def test_public_category_request_prioritizes_candidates_without_inflating_paid_forecast(self):
        cat=fixture_catalog();cat['s1'].update(self.cat['s1'])
        self.feed([self.request()]);demand.ingest(self.root,cat,self.now)
        old=Path.cwd();os.chdir(self.root)
        try:
            rows=portfolio.candidates(cat,fixture_inventory(0),[],self.now,set(),random.Random(1))
        finally:os.chdir(old)
        row=next(r for r in rows if r['sku']=='s1')
        self.assertEqual(row['forum_intent']['request_ids'],[77]);self.assertEqual(row['confirmed_settlements_12h'],0);self.assertEqual(row['observed_requests_12h'],0)
    def test_model_approval_and_emitted_supply_keep_original_forum_request_ids(self):
        cat=fixture_catalog();cat['s1'].update(self.cat['s1'])
        self.feed([self.request()]);demand.ingest(self.root,cat,self.now)
        captured=[]
        def model(role,prompt,schema):
            anchor=prompt['exploration_anchor_sku'];chosen=list(dict.fromkeys([anchor,'s1']))
            chosen += [r['sku'] for r in prompt['candidates'] if r['sku'] not in chosen][:prompt['maximum_new_skus']-len(chosen)]
            captured.append(prompt)
            return {'action':'buy','skus':chosen,'reason':'Test explicit forum sourcing approval'}
        old=Path.cwd();os.chdir(self.root)
        try:
            inv=fixture_inventory(0)
            result=portfolio.run(inv,cat,[],self.now,model=model,refresh=lambda:(inv,self.now,True),
                rng=random.Random(1),state_path=Path('state/test-portfolio.json'))
        finally:os.chdir(old)
        self.assertIn('s1',[r['sku'] for r in result['buy']])
        state=json.loads((self.root/'state/test-portfolio.json').read_text())
        self.assertEqual(next(r for r in state['intents'] if r['sku']=='s1')['forum_request_ids'],[77])
        from market_memory import events
        approval=next(e for e in events(self.root/'logs/shop-events.jsonl') if e['event']=='portfolio_approval')
        self.assertEqual(next(r for r in approval['approved_lines'] if r['sku']=='s1')['forum_request_ids'],[77])

    def test_stale_feed_is_not_ingested_and_old_requests_expire(self):
        self.feed([self.request()]);self.assertEqual(demand.ingest(self.root,self.cat,self.now+91)['reason'],'forum_stale')
        demand.ingest(self.root,self.cat,self.now)
        self.assertEqual(demand.active(self.root,self.now+demand.TTL+1),[])

if __name__=='__main__':unittest.main()
