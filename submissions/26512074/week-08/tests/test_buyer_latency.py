import io
import json
import time
import unittest
from unittest.mock import patch,AsyncMock
import buyer_r3


class BuyerLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_nearby_preflights_share_snapshot_and_expired_cache_refreshes(self):
        row=(1,dict(url='shop'), 'x',100);stock=[1]
        def fetch(*a,**kw):return io.BytesIO(json.dumps({'shops':[dict(url='shop',stock={'x':stock[0]})]}).encode())
        with patch.object(buyer_r3,'_stock_cache',{}),patch('urllib.request.urlopen',side_effect=fetch) as request:
            self.assertEqual(await buyer_r3.refresh_stock([row]),[row])
            self.assertEqual(await buyer_r3.refresh_stock([row]),[row]);self.assertEqual(request.call_count,1)
            buyer_r3._stock_cache['at']=time.monotonic()-3;stock[0]=0
            self.assertEqual(await buyer_r3.refresh_stock([row]),[]);self.assertEqual(request.call_count,2)

    async def test_impossible_price_target_does_not_send_zero_bid(self):
        calls=[]
        class Client:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,text,*a):
                calls.append(text)
                return dict(id='t',status=dict(state='TASK_STATE_INPUT_REQUIRED',message=dict(parts=[dict(text='asking 80, grade A')])) )
        shops=[dict(name='shop',url='shop',offers=[dict(sku='x',qty=1)])]
        with patch.object(buyer_r3,'A2AClient',Client),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda r:r)),patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda r,*a,**kw:r)),patch.object(buyer_r3,'price_ceiling',return_value=0):
            result=await buyer_r3.buy(shops,dict(acceptable=['x'],budget=100,expires=time.time()+60),{'x':dict(name='X',kind='goods',list_price=100)})
        self.assertIsNone(result['receipt']);self.assertEqual(len(calls),1);self.assertNotIn('0',calls)
