"""Seller remains bounded and honest when the optional API harness returns no decision."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import httpx,seller_r3
class SellerFallbackTests(unittest.IsolatedAsyncioTestCase):
 async def exchange(self,offers):
  with tempfile.TemporaryDirectory() as tmp:
   unit=dict(unit='offline-fallback',sku='GFALLBACK',name='Fallback fixture',category='test',grade='A',kind='goods',cost=50,list_price=100,expires_at=None,supplied_at=1)
   Path(tmp,'inventory.json').write_text(json.dumps({'round':3,'units':[unit]}))
   tasks=[]
   with patch.object(seller_r3,'MARKET_DIR',Path(tmp)),patch.object(seller_r3,'ask_json',return_value=(None,'')),patch.object(seller_r3,'sign',side_effect=lambda rec:dict(rec,sig_seller='offline-signature-placeholder')):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999))) as c:
     tid=None
     for text in ['I want to buy GFALLBACK']+offers:
      msg={'role':'ROLE_USER','messageId':str(len(tasks)),'parts':[{'text':text}]}
      if tid:msg['taskId']=tid
      response=await c.post('http://fixture/',headers={'A2A-Version':'1.0'},json={'jsonrpc':'2.0','id':'1','method':'SendMessage','params':{'message':msg}})
      t=response.json()['result']['task'];tid=t['id'];tasks.append(t)
   return tasks
 @patch.object(seller_r3, 'FASTPATH', False)   # model-specific: harness path
 async def test_harness_absent_accepts_exact_current_ask_without_model_claim(self):
  tasks=await self.exchange(['100'])
  self.assertEqual(tasks[0]['status']['state'],'TASK_STATE_INPUT_REQUIRED')
  self.assertEqual(tasks[1]['status']['state'],'TASK_STATE_COMPLETED')
  receipt=tasks[1]['artifacts'][0]['parts'][0]['data']
  self.assertEqual(receipt['price'],100)
  self.assertEqual(receipt['unit'],'offline-fallback')
 async def test_harness_absent_does_not_accept_below_ask_or_below_cost(self):
  tasks=await self.exchange(['40','60','100'])
  self.assertEqual([t['status']['state'] for t in tasks],['TASK_STATE_INPUT_REQUIRED']*3+['TASK_STATE_COMPLETED'])
if __name__=='__main__':unittest.main()
