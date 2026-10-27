"""Real memory receives only authenticated kit feedback, isolated from production."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from negotiation.experience import Experience
from negotiation.market import choose_shop, review_trade


spec=importlib.util.spec_from_file_location('memory_runtime_test',Path(__file__).parents[1]/'agent-market'/'run-kit.py')
runtime=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class MemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.memory=Experience(self.root/'state.sqlite3')

    def tearDown(self):
        self.folder.cleanup()

    def test_buyer_feedback_requires_verified_payment_not_just_signature(self):
        row={'name':'s','item':'lamp','budget':140,'price':110,'outcome':'purchased',
             'task_id':'t','epoch':100,'sequence':1,'signature_verified':True,'seconds':2,'turns':1}
        self.memory.buyer_observation(row,round_number=3)
        response=httpx.Response(200,json={'verified':True,'checks':{},'ledger':1,'review_accepted':True})
        for order,proof,expected in [('unpaid',None,0),('paid',{'ledger':1},1)]:
            body={'order':order,'shop':'https://s.example','receipt':{'task':'t'},'mandate_receipt':proof}
            with patch.object(runtime,'AGENT',self.root),patch.object(runtime,'experience',self.memory), \
                 patch.object(runtime,'original_call',return_value=response),patch.dict(os.environ,{'MARKET_ROUND':'3'}):
                runtime.market_call(None,'POST','/api/results',json=body)
            self.assertEqual(self.memory.buyer_summary('lamp',140,round_number=3)['s']['eligible_successes'],expected)

    def test_seller_feedback_binds_to_its_signed_checkout_and_failed_settle_is_ignored(self):
        key=Ed25519PrivateKey.generate()
        checkout={'task':'t','item':'lamp','price':115}
        body={'order':'o','checkout_jwt':runtime.kit.jwt_sign(key,checkout)}
        for status in [402,200]:
            response=httpx.Response(status,json={'mandate_receipt':{'ledger':5},'ledger':5})
            with patch.object(runtime,'AGENT',self.root),patch.object(runtime,'experience',self.memory), \
                 patch.object(runtime,'original_call',return_value=response),patch.object(runtime.kit,'load_key',return_value=key):
                runtime.market_call(None,'POST','/api/settle','shop',json=body)
            self.assertEqual(self.memory.status().get('seller_settled',0),int(status==200))

    def test_r3_selection_uses_paid_history_instead_of_receipt_only_success(self):
        shops=[{'name':n,'url':'https://'+n+'.example','reviews':[]} for n in ['unpaid','paid']]
        history=[{'name':n,'item':'lamp','outcome':'purchased','price':100,'seconds':2,
                  'market_verified':True,'signature_verified':True,'market_settled':n=='paid'} for n in ['unpaid','paid']]
        chosen,_,_=choose_shop(shops,history,'lamp',140,require_verified=True,require_settled=True)
        self.assertEqual(chosen['name'],'paid')

    def test_review_does_not_encode_private_budget_in_rating_or_text(self):
        result={'outcome':'purchased','receipt':{'price':100}}
        self.assertEqual(review_trade(result,None,105,1,reveal_budget=False),
                         review_trade(result,None,190,1,reveal_budget=False))
        stars,text=review_trade({},'worker_failed',140,0,local_failure=True)
        self.assertEqual(stars,3)
        self.assertIn('My decision service',text)


if __name__=='__main__':unittest.main()
