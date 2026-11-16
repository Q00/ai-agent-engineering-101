import json
from pathlib import Path
import tempfile
import unittest
from buyer_memory import PreparedHistory
from buyer_policy import routing_evidence
import buyer_breakage as b

class VerifiedBreakageTests(unittest.TestCase):
    now=1800000000
    def sale(self,n=1,shop='shop',sku='G',paid=True):
        return {'event':'settled' if paid else 'negotiation','order':f'o{n}','sku':sku,'ledger':n if paid else None,
                'price':800,'at':self.now-3600,'shop':'https://old','shop_name':shop,'grade':'A'}
    def proof(self,n=1,refunded=0,**changes):
        row={'seq':100+n,'ts':self.now-10,'kind':'broken','round':3,
             'data':{'buyer':b.OWN_NAME,'order':f'o{n}','unit':f'u{n}','item':'G','shop':'https://new','refunded':refunded}}
        row.update(changes);return row
    def test_exact_order_survives_url_change_and_duplicates_do_not_double_charge(self):
        rows=[self.sale()];proof=self.proof();facts=b.attach(rows,[proof,proof],now=self.now)
        self.assertEqual(len(facts),1);self.assertEqual(facts[0]['shop_name'],'shop')
        profile=b.profiles(PreparedHistory(rows+facts+facts),now=self.now)
        self.assertEqual(profile['shop_sku'][('shop','G')],{'paid':1,'broken':1,'refunded':0,'mature_paid':0,'early_broken':1})
    def test_other_buyer_sku_or_unpaid_receipt_cannot_label_shop(self):
        p=self.proof();p['data']['buyer']='someone else'
        self.assertFalse(b.attach([self.sale()],[p],now=self.now))
        p=self.proof();p['data']['item']='different'
        self.assertFalse(b.attach([self.sale()],[p],now=self.now))
        self.assertFalse(b.attach([self.sale(paid=False)],[self.proof()],now=self.now))
    def test_refund_has_smaller_saved_debit_and_other_sku_is_not_vetoed(self):
        q={'same_shop_sku':{'paid':4,'broken':1,'refunded':0}}
        r={'same_shop_sku':{'paid':4,'broken':1,'refunded':1}}
        self.assertGreater(b.contribution_debit(q,.8),b.contribution_debit(r,.8))
        self.assertLess(routing_evidence(None,.8,q)['estimated_saved_per_attempt'],routing_evidence(None,.8)['estimated_saved_per_attempt'])
        rows=[self.sale()];facts=b.attach(rows,[self.proof()],now=self.now)
        p=b.profiles(PreparedHistory(rows+facts),now=self.now)
        self.assertEqual(b.contribution_debit(b.for_choice(p,'shop','OTHER'),.8),0)
    def test_name_only_notice_has_no_shop_penalty_and_overlapping_evidence_is_not_doubled(self):
        rows=[self.sale(),{'event':'broken','name':'same product','broke_at':self.now-5}]
        p=b.profiles(PreparedHistory(rows),now=self.now)
        self.assertEqual(b.contribution_debit(b.for_choice(p,'shop','G'),.8),0)
        cohort={'paid':4,'broken':1,'refunded':0}
        self.assertEqual(b.contribution_debit({'same_shop_sku':cohort},.8),b.contribution_debit({'same_shop_sku':cohort,'same_product':cohort},.8))
    def test_old_failure_expires_and_live_and_backfill_sources_are_joined_readonly(self):
        old=self.proof();old['ts']=self.now-b.WINDOW-1
        self.assertFalse(b.collect([],[old],b.OWN_NAME,self.now))
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);runtime=state/'buyer-runtime';(runtime/'logs').mkdir(parents=True);(runtime/'state').mkdir()
            path=runtime/'state/buyer-breakage-backfill.json';payload=json.dumps({'rows':[self.proof()]});path.write_text(payload)
            facts=b.load([self.sale()],runtime/'logs/buyer-events.jsonl',now=self.now)
            self.assertEqual(len(facts),1);self.assertEqual(path.read_text(),payload)

    def test_real_candidate_ranking_uses_shop_sku_breakages_and_keeps_all_options(self):
        import time
        from unittest.mock import patch
        from market_buyer_r3 import candidates
        now=time.time()
        rows=[]
        for shop,offset in [('bad',0),('other',10)]:
            for i in range(1,4):
                e=self.sale(i+offset,shop=shop);e['at']=now-3600;rows.append(e)
        proof=self.proof();proof['ts']=now-10
        facts=b.attach(rows,[proof],now=now)
        history=PreparedHistory(rows+facts)
        shops=[{'name':shop,'url':'https://'+shop,'offers':[{'sku':'G','qty':5}]} for shop in ['bad','other']]
        order={'kind':'goods','acceptable':['G'],'budget':1000}
        catalog={'G':{'kind':'goods','list_price':1000,'name':'Example','category':'goods'}}
        found=candidates(order,shops,catalog,'own',full_pool=True,history=history)
        self.assertEqual(len(found),2)
        by_shop={e['shop']:e for e in found}
        self.assertLess(by_shop['bad']['routing_evidence']['estimated_saved_per_attempt'],by_shop['other']['routing_evidence']['estimated_saved_per_attempt'])
        self.assertEqual(found[0]['shop'],'other')

    def test_fresh_ledger_collector_is_owner_scoped_bounded_and_preserves_unique_proofs(self):
        import counterparty_memory
        from unittest.mock import patch,Mock
        proof=self.proof();other=self.proof(2);other['data']['buyer']='different'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cache.json';event=Path(directory)/'events.jsonl'
            response=Mock();response.raise_for_status.return_value=None;response.json.return_value=[proof,other]
            with patch.object(counterparty_memory.httpx,'get',return_value=response):
                counterparty_memory.refresh(b.OWN_NAME,cache_path=path,event_path=event,now=self.now)
            cached=json.loads(path.read_text())
            self.assertEqual([e['seq'] for e in cached['buyer_breakages']],[proof['seq']])
            self.assertEqual(len(b.collect(cached['buyer_breakages'],[proof],b.OWN_NAME,self.now)),1)
