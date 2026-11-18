import unittest
from unittest.mock import patch,AsyncMock
import buyer_r3


def task(state,text='',price=None):
    t={'id':'t','status':{'state':state,'message':{'parts':[{'text':text}]}}}
    if price is not None:
        t['artifacts']=[{'parts':[{'data':{'shop':'s','item':'x','price':price,'task':'t','sig_seller':'placeholder'}}]}]
    return t

class FakeClient:
    scripts={}
    visits=[]
    def __init__(self,url,**kwargs):
        self.url=url
        self.answers=list(self.scripts[url])
    async def __aenter__(self):
        self.visits.append(self.url)
        return self
    async def __aexit__(self,*args):
        pass
    async def discover(self):
        return {}
    async def send(self,*args):
        return self.answers.pop(0)
    async def cancel(self,*args):
        return {}

class BuyerRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stock_patch=patch.object(buyer_r3,'refresh_stock',new=AsyncMock(side_effect=lambda rows:rows))
        self.stock_patch.start();self.addCleanup(self.stock_patch.stop)

    def test_clearance_food_keeps_actual_use_deadline(self):
        from buyer_compare import food_min_hours
        self.assertAlmostEqual(food_min_hours({'use_within_hours':2}),2)
        self.assertEqual(food_min_hours({}),0)

    def test_out_of_stock_variants_trigger_sku_cooldown(self):
        memory={}
        payments=[dict(shop='s',item='x',order=str(i),paid=False,reason='409 currently out of stock') for i in range(2)]
        buyer_r3.checkout_feedback(payments,memory,1000)
        self.assertEqual(memory['s']['stock_cooldowns']['x']['until'],1300)

    async def test_food_shelf_fraction_does_not_override_actual_use_requirement(self):
        FakeClient.visits=[]
        FakeClient.scripts={'http://a':[task('TASK_STATE_INPUT_REQUIRED','0.001 h of shelf life left. asking 20'),task('TASK_STATE_COMPLETED',price=1)]}
        with patch.object(buyer_r3,'A2AClient',FakeClient):
            result=await buyer_r3.buy([dict(url='http://a',name='a',offers=[dict(sku='x',qty=1)])],
                                    dict(budget=100,acceptable=['x'],kind='food',shelf_hours=1440),
                                    {'x':dict(list_price=100,name='X',kind='food')})
        self.assertNotIn('min_hours_left',result['accept'])
        self.assertIsNotNone(result['receipt'])

    async def run_case(self,first,order=None):
        FakeClient.visits=[]
        FakeClient.scripts={'http://a':first,'http://b':[
            task('TASK_STATE_INPUT_REQUIRED','1000 h of shelf life left. asking 80'),
            task('TASK_STATE_INPUT_REQUIRED','1000 h of shelf life left. asking 80'),
            task('TASK_STATE_INPUT_REQUIRED','1000 h of shelf life left. asking 80'),
            task('TASK_STATE_INPUT_REQUIRED','1000 h of shelf life left. asking 80'),
            task('TASK_STATE_COMPLETED',price=40)]}
        shops=[{'url':'http://'+s,'name':s,'offers':[{'sku':'x','qty':1}]} for s in ['a','b']]
        memory={}
        # Retry/receipt tests isolate transport failures from the live price
        # model; the separate budget/score tests cover ceiling calculation.
        with patch.object(buyer_r3,'price_ceiling',return_value=80),patch.object(buyer_r3,'compare',new=AsyncMock(side_effect=lambda rows,*a,**k:rows)), patch.object(buyer_r3,'A2AClient',FakeClient):
            result=await buyer_r3.buy(shops,order or {'budget':100,'acceptable':['x']},
                                       {'x':{'list_price':100,'name':'X'}},memory=memory)
        self.assertEqual(result['shop'],'http://b')
        self.assertEqual(result['receipt']['price'],40)
        self.assertEqual(FakeClient.visits,['http://a','http://b'])
        return memory
    async def test_rejection_tries_next_shop(self):
        await self.run_case([task('TASK_STATE_REJECTED')])

    async def test_stock_disappearing_after_receipt_tries_next_shop(self):
        checks=0
        async def fresh(rows):
            nonlocal checks
            if len(rows)==1 and rows[0][1]['url']=='http://a':
                checks+=1
                if checks>=2:return []
            return rows
        with patch.object(buyer_r3,'refresh_stock',new=AsyncMock(side_effect=fresh)):
            await self.run_case([task('TASK_STATE_INPUT_REQUIRED','asking 80') for _ in range(4)]+[task('TASK_STATE_COMPLETED',price=40)])
    async def test_overcharge_blocked_and_next_shop_used(self):
        memory=await self.run_case([task('TASK_STATE_INPUT_REQUIRED','asking 80'),
             task('TASK_STATE_INPUT_REQUIRED','asking 80'),task('TASK_STATE_COMPLETED',price=90)])
        self.assertIn('receipt violation',memory['http://a']['reason'])
    async def test_insufficient_food_lifetime_tries_next(self):
        await self.run_case([task('TASK_STATE_INPUT_REQUIRED','345 h of shelf life left. asking 80')],
                            {'budget':100,'acceptable':['x'],'kind':'food','use_within_hours':540})
    async def test_certificate_refusal_tries_next(self):
        end=task('TASK_STATE_COMPLETED',price=40)
        end['artifacts'][0]['parts'][0]['data']['cert']=False
        memory=await self.run_case([task('TASK_STATE_INPUT_REQUIRED','asking 80') for _ in range(4)]+[end])
        self.assertIn('certificate',memory['http://a']['reason'])

    async def test_unknown_food_lifetime_keeps_checkout_deadline(self):
        FakeClient.visits=[]
        FakeClient.scripts={'http://a':[task('TASK_STATE_INPUT_REQUIRED','asking 80') for _ in range(4)]
                            +[task('TASK_STATE_COMPLETED',price=40)]}
        with patch.object(buyer_r3,'A2AClient',FakeClient):
            result=await buyer_r3.buy([dict(url='http://a',name='a',offers=[dict(sku='x',qty=1)])],
                                    dict(budget=100,acceptable=['x'],kind='food',use_within_hours=12),
                                    {'x':dict(list_price=100,name='X',kind='food')})
        self.assertEqual(result['receipt']['price'],40)
        self.assertEqual(result['accept']['min_hours_left'],12)

if __name__=='__main__':
    unittest.main()
