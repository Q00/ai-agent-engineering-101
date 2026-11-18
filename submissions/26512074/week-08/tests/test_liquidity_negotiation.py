import tempfile,unittest
from unittest.mock import AsyncMock,patch
import buyer_r3
from optimizer import db,negotiate


class LiquidityNegotiationTests(unittest.TestCase):
    def test_responses_without_settlements_do_not_force_more_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            with db(directory) as c:
                for i in range(20):
                    c.execute('insert into trials(task,sku,at,reference,responded,inquiry) values(?,?,?,?,?,0)',(str(i),'x',1000,100,1))
            unit=dict(sku='x',kind='goods',cost=100000,list_price=300000,supplied_at=2000)
            move=negotiate(directory,'new',dict(unit=unit,ask=160000,offer=140000,lines=['buyer:140000']),dict(floor=101000,ask=160000),2000)
            self.assertEqual(move['action'],'accept')

    def test_opportunity_cost_never_overrides_loss_floor(self):
        with tempfile.TemporaryDirectory() as directory:
            unit=dict(sku='x',kind='goods',cost=100000,list_price=300000,supplied_at=2000)
            move=negotiate(directory,'new',dict(unit=unit,ask=160000,offer=90000,lines=['buyer:90000']),dict(floor=101000,ask=160000),2000)
            self.assertNotEqual(move['action'],'accept')


class BuyerAlternativeTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_acceptable_sku_at_same_shop_is_still_tried(self):
        client=AsyncMock();client.__aenter__.return_value=client
        client.send.return_value=dict(id='t',status=dict(state='TASK_STATE_REJECTED',message=dict(parts=[dict(text='out of stock')])))
        shop=dict(name='s',url='http://s',offers=[dict(sku='x',qty=1),dict(sku='y',qty=1)])
        with patch.object(buyer_r3,'A2AClient',return_value=client):
            result=await buyer_r3.buy([shop],dict(acceptable=['x','y'],budget=100),{sku:dict(name=sku,list_price=100) for sku in ('x','y')})
        self.assertIsNone(result['receipt'])
        self.assertEqual(client.send.await_count,2)
