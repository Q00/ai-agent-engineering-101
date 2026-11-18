import unittest
from unittest.mock import AsyncMock,patch
import buyer_r3


class FoodStockGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_clearance_price_does_not_override_remaining_life(self):
        client=AsyncMock();client.__aenter__.return_value=client
        client.send.return_value={'id':'t','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'text':'asking 1; 2 hours of shelf life left'}]}}}
        with patch.object(buyer_r3,'A2AClient',return_value=client):
            result=await buyer_r3.buy([dict(name='seller',url='http://s',offers=[dict(sku='x',qty=1)])],
                dict(kind='food',acceptable=['x'],budget=100,use_within_hours=1,shelf_hours=24),
                {'x':dict(name='X',list_price=100,kind='food')})
        self.assertIsNone(result['receipt'])
        self.assertEqual(result['accept']['min_hours_left'],1)
        self.assertGreaterEqual(client.send.await_count,1)
