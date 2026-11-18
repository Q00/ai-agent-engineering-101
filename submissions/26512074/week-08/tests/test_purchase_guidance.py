import json,tempfile,unittest
from pathlib import Path
from optimizer import db
from purchase_guidance import quote_suffix,evaluate
from policy import read_price

class GuidanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_assignment_current_terms_and_price_parser(self):
        with tempfile.TemporaryDirectory() as d:
            unit={'sku':'G12345678','grade':'A'}
            Path(d,'purchase-guidance-policy.json').write_text('{"share":80}')
            task=next(str(i) for i in range(100) if __import__('zlib').crc32(str(i).encode())%100<80)
            suffix=quote_suffix(d,task,unit,115000,6,True,1000)
            self.assertIn('accept_current_quote_if_eligible',suffix)
            self.assertEqual(await read_price('Counteroffer: 115000.'+suffix,role='ask',allow_model=False),115000)
            Path(d,'purchase-guidance-policy.json').write_text('{"share":0}')
            self.assertIn('"quoted_price":110000',quote_suffix(d,task,unit,110000,6,False,1001))
            self.assertEqual(quote_suffix(d,'legacy',unit,110000,6,False,1001),'')

    async def test_control_and_real_settlement_refund_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d,'purchase-guidance-policy.json').write_text('{"share":0}')
            self.assertEqual(quote_suffix(d,'t',{'sku':'x','grade':'B'},200,6,True,1000),'')
            with db(d) as c:
                c.execute('insert into trials(task,sku,unit,at,inquiry,first_offer,negotiation_rounds,settled_at,price,cost,margin) values(?,?,?,?,?,?,?,?,?,?,?)',('t','x','u',1000,0,180,2,1100,190,100,90))
            events=[dict(seq=n,round=3,kind=k,data=dict(shop='totato2203-r3',unit='u',amount=190,refunded=190)) for n,k in enumerate(('broken','refund'))]
            Path(d,'ledger.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            r=evaluate(d,2000)
            self.assertEqual(r['A']['settled'],1)
            self.assertEqual(r['A']['observed_refunds'],190)
            self.assertEqual(r['A']['gross_after_observed_refunds'],-100)
            self.assertEqual(r['decision'],'hold_insufficient_evidence')
