import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from after_sales import clearance,service,warranty_offer,release_warranty
from shop_profit import recovery_price
import recommendations as r
from types import SimpleNamespace
from unittest.mock import AsyncMock,MagicMock
import seller_r3

class WarrantyExecutionTests(unittest.IsolatedAsyncioTestCase):
 async def test_requested_warranty_is_in_receipt_and_reserved(self):
  with tempfile.TemporaryDirectory() as d:
   md=Path(d)
   (md/'inventory.json').write_text(json.dumps({'balance':1000000,'units':[]}))
   seller=seller_r3.Seller()
   unit={'unit':'u','sku':'x','name':'X','kind':'goods','cost':50,'list_price':100}
   seller.open['t']={'unit':unit,'ask':70,'offer':None,'lines':[],'warranty_hours':6}
   updater=MagicMock()
   updater.add_artifact=AsyncMock();updater.complete=AsyncMock()
   context=SimpleNamespace(current_task=SimpleNamespace(id='t',context_id='c'),message=None)
   with patch.object(seller_r3,'MARKET_DIR',md),patch.object(seller_r3,'TaskUpdater',return_value=updater), \
        patch.object(seller_r3,'get_message_text',return_value='80'),patch.object(seller_r3,'sign',side_effect=lambda r:r), \
        patch.object(seller_r3,'new_data_part',side_effect=lambda r:r):
    await seller.execute(context,None)
   receipt=updater.add_artifact.call_args.kwargs['parts'][0]
   self.assertEqual(receipt['warranty_hours'],6)
   self.assertEqual(receipt['price'],80)
   self.assertTrue(json.loads((md/'warranty-holds.json').read_text())['u']['issued'])

class ClearanceSupportTests(unittest.TestCase):
 def test_expiry_floor_accepts_reasonable_salvage_and_never_expired(self):
  unit={'sku':'x','unit':'u','kind':'food','name':'Food','grade':'C','cost':100,'list_price':150,'expires_at':8200}
  p=recovery_price(unit,{'balance':1000000,'units':[unit]},[],1000)
  self.assertEqual(p['floor'],20)
  self.assertLess(p['floor'],p['ask'])
  self.assertEqual(len(clearance({'units':[unit]},[],1000)),1)
  self.assertEqual(clearance({'units':[unit]},[],8200),[])
 def test_warranty_fully_reserved_and_persistent(self):
  with tempfile.TemporaryDirectory() as d:
   u={'unit':'u','kind':'goods'}
   self.assertTrue(warranty_offer(d,u,100000,1000000,1000))
   self.assertFalse(warranty_offer(d,{'unit':'v','kind':'goods'},200000,1000000,1000))
   release_warranty(d,'u',issued=True,price=100000)
   release_warranty(d,'u')
   self.assertTrue(json.loads((Path(d)/'warranty-holds.json').read_text())['u']['issued'])
 def test_support_request_only_after_actual_own_refund(self):
  event={'seq':5,'kind':'broken','ts':900,'data':{'shop':'own','item':'x','buyer':'buyer','refunded':100}}
  with tempfile.TemporaryDirectory() as d,patch('after_sales.ownership_tags',return_value={'own'}):
   result=service(d,{'units':[]},[event],1000,True)
   self.assertEqual(result['status'],'queued')
   post=json.loads((Path(d)/'outbox.jsonl').read_text(encoding='utf-8'))
   self.assertEqual(post['refs'],[5])
   self.assertIn('환불을 확인',post['post'])
  with tempfile.TemporaryDirectory() as d,patch('after_sales.ownership_tags',return_value={'own'}):
   service(d,{'units':[]},[{**event,'data':{**event['data'],'refunded':0}}],1000,True)
   self.assertFalse((Path(d)/'outbox.jsonl').exists())
 def test_comparison_preserves_original_quote(self):
  events=[{'seq':i,'kind':'settle','ts':1000,'data':{'amount':100,'item':'x'}} for i in range(10)]
  f=r.features(events,2,[],'x',1000)
  candidate=r.score(f,{'sku':'x','shop':'other','url':'http://other','price':70,'at':1000},1000)
  with patch.object(r.time,'time',return_value=1000):
   post=r.render_slate([candidate],{'x':{'name':'X'}},{'units':[]},'가격 비교',own_quotes=[{'sku':'x','shop':r.NAME,'price':60,'at':1000}])
  self.assertIn('확인 호가 70',post['post'])
  self.assertIn('실제 확인 호가 60',post['post'])
  self.assertEqual(candidate['price'],70)
if __name__=='__main__':
 unittest.main()
