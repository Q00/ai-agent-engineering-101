import unittest
from unittest.mock import patch
from buyer_compare import compare


def task(identity,text,state='TASK_STATE_INPUT_REQUIRED'):
    return dict(id=identity,status=dict(state=state,message=dict(parts=[dict(text=text)])))


class StrictGradeCompareTests(unittest.IsolatedAsyncioTestCase):
    async def run_compare(self,alternative):
        replies=[task('b','asking 70, grade B, 6-hour warranty'),alternative];cancelled=[];messages=[]
        class Client:
            def __init__(self,*a,**kw):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*a):pass
            async def discover(self):return {}
            async def send(self,text):messages.append(text);return replies.pop(0)
            async def cancel(self,identity):cancelled.append(identity)
        rows=[(1e-6,dict(url='known'), 'x',100),(0,dict(url='unknown'),'x',100)]
        with patch('buyer_compare.A2AClient',Client),patch('buyer_compare.price_ceiling',return_value=100),patch('patch36.settled_price',return_value=None),patch('patch36.outcome',side_effect=lambda v,p,s,g,*a:.92 if g=='A' else .7),patch('patch36.saved_objective',side_effect=lambda s,r:s*r*1e-6):
            result=await compare(rows,dict(budget=100),{'x':dict(kind='goods',name='X',list_price=100)}, {},limit=1)
        return result[0][1]['_comparison_quote'],cancelled,messages

    async def test_explicit_A_quote_can_beat_cheaper_B(self):
        quote,cancelled,messages=await self.run_compare(task('a','asking 80, grade A, 6-hour warranty'))
        self.assertEqual(quote['grade'],'A');self.assertEqual(quote['ask'],80)
        self.assertEqual(cancelled,['b','a']);self.assertIn('Only grade A',messages[1])

    async def test_unavailable_A_keeps_usable_B_quote(self):
        quote,cancelled,_=await self.run_compare(task('a','none available','TASK_STATE_REJECTED'))
        self.assertEqual(quote['grade'],'B');self.assertEqual(cancelled,['b'])

    async def test_conflicting_grade_claim_is_not_assumed_A(self):
        quote,_,_=await self.run_compare(task('a','You asked for grade A; asking 80 for grade B'))
        self.assertEqual(quote['grade'],'B')
