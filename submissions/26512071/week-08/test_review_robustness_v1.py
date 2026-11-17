"""Reproducible independent-review findings; no market/model requests."""
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import buyer_r3,restock_r3,public_evidence,forum_r3,seller_r3
from test_buyer_policy_v1 import negotiate,offers
from test_restock_policy_v1 import write_state,goods
from test_seller_policy_v1 import UNIT
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
class BuyerRaiseTests(unittest.IsolatedAsyncioTestCase):
    async def test_lower_then_raise_is_not_chased(self):
        out,seen=await negotiate([('ask',100,'t'),('ask',95,'t'),('ask',100,'t'),('done',100,'t')],Ed25519PrivateKey.generate(),120)
        self.assertEqual(offers(seen),[90,95])
        self.assertIsNone(out['receipt'])
class RestockRobustTests(unittest.TestCase):
    def test_closed_window_missing_open_time_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);write_state(p,{'G1':goods('cat')},inv={'round':3,'start':5000000,'balance':5000000,'units':[],'restock':{'open':False}})
            self.assertEqual(restock_r3.plan(p,now=100),{'buy':[]})
    def test_journal_failure_does_not_create_phantom_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);write_state(p,{'G1':goods('cat')})
            with patch.object(restock_r3,'_decide',side_effect=OSError('fixture observation failure')):
                result=restock_r3.plan(p,now=100)
            pending=json.loads((p/'restock-pending.json').read_text())
            self.assertEqual([r['sku'] for r in result['buy']],[r['sku'] for r in pending])
class PublicRobustTests(unittest.TestCase):
    def test_response_read_is_bounded(self):
        class Response(io.BytesIO):
            def read(self,size=-1):
                self.limit=size;return super().read(size)
        response=Response(b'[]')
        with patch('public_evidence.urllib.request.urlopen',return_value=response):
            public_evidence._fetch()
        self.assertGreater(response.limit,0)
        self.assertLessEqual(response.limit,2000001)
    def test_forum_requires_real_top_level_round_and_amount(self):
        paid={'ledger':1,'order':'o1','shop':'https://other.test','item':'G1','price':100}
        base={'seq':1,'kind':'settle','round':3,'data':{'buyer':'fair-furniture','order':'o1','shop':'https://other.test','item':'G1','amount':100}}
        wrong=json.loads(json.dumps(base));wrong['data']['price']=wrong['data'].pop('amount')
        self.assertIsNone(forum_r3.public_match([wrong],paid))
        wrong=json.loads(json.dumps(base));wrong['data']['round']=wrong.pop('round')
        self.assertIsNone(forum_r3.public_match([wrong],paid))
    def test_corrupt_forum_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'forum-policy.json').write_text('{"enabled":true}');(p/'forum-report.json').write_text('{"intents":{},"content":null}')
            # No paid data is also safe; deliberately supply paid facts to reach corrupt content.
            paid={'ledger':1,'order':'o1','shop':'https://other.test','item':'G1','price':100,'paid':True}
            (p/'payments.jsonl').write_text(json.dumps(paid)+'\n')
            row={'seq':1,'kind':'settle','round':3,'data':dict(buyer='fair-furniture',order='o1',shop='https://other.test',item='G1',amount=100)}
            try:result=forum_r3.maybe_report(p,now=100000,rows=[row])
            except (TypeError,AttributeError) as exc:self.fail('Corrupt record raised '+type(exc).__name__)
            self.assertFalse(result.get('queued',False))
class SellerRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_hold_survives_task_removal_during_artifact_await(self):
        seller=seller_r3.Seller();tid='race-owned-task';unit=dict(UNIT)
        seller.open[tid]={'unit':unit,'ask':90,'offer':None,'first':90,'src':{'task':tid,'unit':unit['unit'],'sku':unit['sku'],'cost':unit['cost'],'snapshot':'fixture'},'lines':[],'touched':100}
        class Updater:
            def __init__(self,**kwargs):pass
            def new_agent_message(self,**kwargs):return kwargs
            async def add_artifact(self,**kwargs):seller.open.pop(tid,None)
            async def complete(self,*args):pass
        with tempfile.TemporaryDirectory() as tmp, patch.object(seller_r3,'MARKET_DIR',Path(tmp)),patch.object(seller_r3,'now',return_value=100),patch.object(seller_r3,'inventory',return_value=({'round':3,'units':[unit]},'fixture')),patch.object(seller_r3,'get_message_text',return_value='100'),patch.object(seller_r3,'ask_json',return_value=({'action':'accept'},'fixture')),patch.object(seller_r3,'TaskUpdater',Updater),patch.object(seller_r3,'sign',side_effect=lambda r:r):
            context=SimpleNamespace(current_task=SimpleNamespace(id=tid,context_id='c'),message=None)
            try:await seller.execute(context,None)
            except KeyError:self.fail('Concurrent task removal loses completion hold')
        self.assertIn(unit['unit'],seller.held)
