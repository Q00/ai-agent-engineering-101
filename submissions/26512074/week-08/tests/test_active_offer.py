import json,hashlib,tempfile,unittest
from pathlib import Path
from optimizer import db
from purchase_guidance import quote_suffix
from active_offer import target_offer,decision
from policy import read_price
from seller_r3 import offer_price

class ActiveOfferTests(unittest.IsolatedAsyncioTestCase):
    async def test_200_percent_proposal_keeps_quote_parser_and_explicit_bid(self):
        from active_offer import build_active_offer_guidance
        text='Counteroffer: 100001.'+build_active_offer_guidance({'sku':'x','grade':'A'},100001,6)
        self.assertEqual(target_offer(100001),200002)
        self.assertEqual(await read_price(text,role='ask',allow_model=False),100001)
        self.assertEqual(await offer_price('200002',100001),200002)
        self.assertEqual(await offer_price('I accept',100001),100001)
        data=json.loads(text[text.index('{'):])['buyer_action_plan']
        self.assertEqual(data['quoted_price'],100001);self.assertEqual(data['proposed_purchase_amount'],200002)

    async def test_fixed_three_groups_preserve_old_assignments_and_price_policy(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);u={'sku':'x','grade':'A'}
            quote_suffix(d,'old',u,100,0,True,1000)
            pricing='{"enabled":true,"multiplier":2,"allocation":20,"experiment_started_at":500}'
            (p/'extreme-quote-policy.json').write_text(pricing)
            (p/'active-offer-policy.json').write_text('{"started_at":1001,"allocations":{"G0":30,"G1":30,"G2":40}}')
            for arm,lo,hi in [('G0',0,30),('G1',30,60),('G2',60,100)]:
                task=next(str(i) for i in range(1000) if lo<=int.from_bytes(hashlib.sha256(('active-offer-v1:'+str(i)).encode()).digest()[:8],'big')%100<hi)
                suffix=quote_suffix(d,task,u,100,6,True,1002)
                self.assertEqual('AUTONOMOUS BUYER ACTION PLAN' in suffix,arm=='G2')
                self.assertEqual(bool(suffix),arm!='G0')
                with db(d) as c:self.assertEqual(c.execute('select arm from active_offers where task=?',(task,)).fetchone()[0],arm)
            quote_suffix(d,'old',u,100,0,False,1003)
            with db(d) as c:self.assertIsNone(c.execute("select 1 from active_offers where task='old'").fetchone())
            self.assertEqual((p/'extreme-quote-policy.json').read_text(),pricing)

    async def test_guidance_deadlines_and_failed_behavior_return_to_control(self):
        g=dict(quotes=40,valid_quotes=40,expected_net_per_quote=100,settled=10,known_cost_sales=10,
               over_ask_offers=0,over_ask_settlements=0,conversion=.25)
        groups={a:dict(g) for a in ('G0','G1','G2')}
        self.assertEqual(decision(groups,2),('RUNNING',None))
        self.assertEqual(decision(groups,6),('RETURN_G0','G0'))
        groups['G2']['valid_quotes']=2
        self.assertEqual(decision(groups,6),('EXTEND_OBSERVATION',None))
        self.assertEqual(decision(groups,12),('RETURN_G0','G0'))
