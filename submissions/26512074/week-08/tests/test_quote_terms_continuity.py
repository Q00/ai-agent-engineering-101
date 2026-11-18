import unittest
from unittest.mock import patch,AsyncMock
import buyer_r3


class QuoteTermsContinuityTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_terms_in_counter_keep_known_grade_and_warranty(self):
        self.assertEqual(buyer_r3.continued_quote_terms('Counteroffer: 473715.','A',72),('A',72))
        self.assertEqual(buyer_r3.continued_quote_terms('grade A, asking 80',None,72),('A',72))

    def test_explicit_change_is_not_inherited_as_old_promise(self):
        self.assertEqual(buyer_r3.continued_quote_terms('grade B, asking 70','A',72),('B',0))
        self.assertEqual(buyer_r3.continued_quote_terms('no warranty; asking 70','A',72),('A',0))
        self.assertEqual(buyer_r3.continued_quote_terms('grade unknown','A',72),(None,0))

    async def test_whole_negotiation_preserves_terms_and_checkout_requirement(self):
        def task(text,state='TASK_STATE_INPUT_REQUIRED'):
            return dict(id='t',status=dict(state=state,message=dict(parts=[dict(text=text)])))
        completed=task('Deal','TASK_STATE_COMPLETED');completed['artifacts']=[dict(parts=[dict(data=dict(task='t',item='x',price=55,claim='A',warranty_hours=6,cert=True,sig_seller='test'))])]
        replies=[task('asking 80, grade A, 72-hour warranty'),task('Counteroffer: 70.'),completed]
        class Client:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):pass
            async def cancel(self,*a):pass
            async def send(self,*a):return replies.pop(0)
        shops=[dict(name='shop',url='shop',offers=[dict(sku='x',qty=1)])]
        # This test targets the legacy loop's ceiling propagation. Isolate it
        # from the real deployment's time-policy flag and live marketplace.
        with patch('strategy_policy.active',return_value=False),patch.object(buyer_r3,'A2AClient',Client),patch.object(buyer_r3,'refresh_stock',AsyncMock(side_effect=lambda r:r)),patch.object(buyer_r3,'compare',AsyncMock(side_effect=lambda r,*a,**kw:r)),patch.object(buyer_r3,'price_ceiling',return_value=100) as ceiling:
            result=await buyer_r3.buy(shops,dict(acceptable=['x'],budget=100),{'x':dict(name='X',list_price=100,kind='goods')})
        self.assertIsNotNone(result['receipt']);self.assertEqual(result['accept']['warranty_hours'],6)
        self.assertGreaterEqual(ceiling.call_count,2)
        self.assertTrue(all(c.args[2]==72 and c.args[6]=='A' for c in ceiling.call_args_list))
