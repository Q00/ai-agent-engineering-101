"""More opportunities without weakening publication truth or market spacing."""
import json,os,unittest
import seller_forum as seller
import buyer_forum as buyer
import test_seller_forum as seller_fixtures
import test_buyer_forum as buyer_fixtures


class SellerActivityTests(unittest.TestCase):
    setUp=seller_fixtures.ShopForumTests.setUp

    def prior(self,age=700,thread=None,skus=()):
        entry={'id':'old','at':self.now-age,'status':'published','campaign':'reply' if thread else 'spotlight',
               'post_id':5,'text':'old','thread':thread,'skus':list(skus),'refs':[]}
        seller.save(self.root/'state/seller-forum.json',{'posts':[entry],'attempts':[]})

    def test_root_post_is_possible_after_ten_minutes(self):
        self.prior()
        calls=iter([self.copy,{'approve':True,'unsupported_claims':[]}])
        seller.run_once(self.root,now=self.now,decide=lambda *a:next(calls))
        state=json.loads((self.root/'state/seller-forum.json').read_text())
        self.assertEqual(state['posts'][-1]['status'],'queued')
        self.assertEqual(state['policy_status']['root_interval_seconds'],600)

    def test_already_replied_request_is_removed_before_model_selection(self):
        self.prior(age=500,thread=42,skus=['s1'])
        state=json.loads((self.root/'state/seller-forum.json').read_text())
        state['posts'].append({'id':'root','at':self.now-400,'status':'published','campaign':'buying_guide','post_id':6,'text':'prior guide','thread':None,'skus':[],'refs':[]})
        seller.save(self.root/'state/seller-forum.json',state)
        (self.root/'.market/forum.json').write_text(json.dumps([{'id':42,'role':'buyer','ts':self.now,'text':'김치 구합니다'}]))
        result=seller.run_once(self.root,now=self.now,decide=lambda *a:self.fail('already-replied request is not an opportunity'))
        self.assertEqual(result['reason'],'no eligible root or reply')
        state=json.loads((self.root/'state/seller-forum.json').read_text())
        self.assertEqual(state['policy_status']['unanswered_requests'],0)

    def test_new_relevant_reply_remains_possible_during_product_ad_cooldown(self):
        self.prior(age=500,thread=42,skus=['s1'])
        self.units[0].update(cost=4000, list_price=10000, expires_at=self.now+24*3600)
        (self.root/'.market/catalog.json').write_text(json.dumps(
            {'s1': {'name': '김치', 'kind': 'food', 'category': '식품', 'shelf_hours': 48, 'list_price': 10000}}))
        inv = self.root/'.market/inventory.json'
        inv.write_text(json.dumps({'round': 3, 'units': self.units}))
        os.utime(inv, (self.now, self.now))
        (self.root/'.market/forum.json').write_text(json.dumps([{'id':43,'role':'buyer','ts':self.now,'text':'김치 구합니다'}]))
        decision=self.copy|{'campaign':'reply','thread_id':43}
        def model(role,prompt,schema):
            if 'checker' in role:return {'approve':True,'unsupported_claims':[]}
            self.assertEqual(prompt['facts']['publication_options']['unanswered_threads'],[43])
            self.assertEqual(prompt['facts']['publication_options']['root_skus'],[])
            quote = prompt['facts']['reply_quotes'][43][0]
            self.assertEqual(quote['unit'], 'free')
            return decision | {'offer_ids': [quote['id']]}
        seller.run_once(self.root,now=self.now,decide=model)
        self.assertEqual(json.loads((self.root/'.market/outbox.jsonl').read_text())['thread'],43)

    def test_generic_buying_tip_is_not_a_buyer_request(self):
        (self.root/'.market/forum.json').write_text(json.dumps([{'id':42,'role':'buyer','ts':self.now,'text':'김치 구할 때 확인 포인트를 추천합니다'}]))
        self.assertEqual(seller.facts(self.root,self.now)['requests'],[])

    def test_self_owned_buyer_posts_are_not_seller_reply_targets(self):
        (self.root/'.market/forum.json').write_text(json.dumps([
            {'id':1,'author':seller.SHOP,'owner':'owner-id','role':'shop','ts':self.now,'text':'가게'},
            {'id':2,'author':'our-buyer','owner':'owner-id','role':'buyer','ts':self.now,'text':'김치 구합니다'}]))
        self.assertEqual(seller.facts(self.root,self.now)['requests'],[])

    def test_product_can_be_rotated_after_ninety_minutes_but_market_limit_stays(self):
        evidence=seller.facts(self.root,self.now)
        seller.validate(self.copy,evidence,[{'at':self.now-5500,'skus':['s1']}],self.now)
        self.prior(age=200)
        result=seller.run_once(self.root,now=self.now,decide=lambda *a:self.fail('market cooldown'))
        self.assertEqual(result['reason'],'market posting cooldown')


class BuyerActivityTests(unittest.TestCase):
    setUp=buyer_fixtures.BuyerForumTests.setUp
    model=buyer_fixtures.BuyerForumTests.model

    def test_new_paid_record_can_publish_after_ten_minutes(self):
        buyer.save(self.root/'state/buyer-forum.json',{'last_attempt':0,'posts':[
            {'id':'old','at':self.now-650,'status':'published','post_id':4,'refs':[99],'skus':['other'],'text':'old'}]})
        result=buyer.run_once(self.root,now=self.now,model=self.model)
        self.assertEqual(result['status'],'queued')
        self.assertEqual(buyer.INTERVAL,600);self.assertEqual(buyer.DAILY_CAP,96)

    def test_five_minute_market_spacing_is_not_bypassed(self):
        buyer.save(self.root/'state/buyer-forum.json',{'last_attempt':0,'posts':[
            {'id':'old','at':self.now-200,'status':'published','post_id':4,'refs':[99],'skus':['other'],'text':'old'}]})
        self.assertEqual(buyer.run_once(self.root,now=self.now,model=lambda *a:self.fail('cooldown'))['status'],'cooldown')


if __name__=='__main__':unittest.main()
