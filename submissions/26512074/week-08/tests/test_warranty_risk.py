import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from warranty_risk import statistics,reserve


class WarrantyRiskTests(unittest.TestCase):
    @patch('warranty_risk.ownership_tags',return_value={'ours'})
    def test_only_mature_warranted_sales_and_deduplicated_refunds(self,_):
        with tempfile.TemporaryDirectory() as d:
            rows=[]
            for i in range(100):
                rows.append(dict(seq=i,round=3,ts=1000,kind='settle',data=dict(shop='ours',unit=str(i),amount=100,warranty_hours=6)))
            for i in range(10):
                for kind in ('broken','refund'):
                    rows.append(dict(seq=kind+str(i),round=3,ts=2000,kind=kind,data=dict(shop='ours',unit=str(i),amount=100,refunded=100)))
            rows.append(dict(seq='recent',round=3,ts=49000,kind='settle',data=dict(shop='ours',unit='new',amount=10000,warranty_hours=6)))
            Path(d,'ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            s=statistics(d,50000)
            self.assertEqual(s['mature_warranted_sales'],100)
            self.assertEqual(s['refunded_sales'],10)
            self.assertAlmostEqual(s['reserve_rate'],.12)
            self.assertLessEqual(reserve(d,10000,50001),1201)

    def test_insufficient_history_keeps_full_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(reserve(d,10000,50000),10000)

    def test_message_experiment_keeps_a_control_window(self):
        from buyer_prompt import message
        with tempfile.TemporaryDirectory() as d:
            self.assertIn('BUYER AGENT ACTION',message(d,1000))
            self.assertIn('like-for-like',message(d,1300))
            self.assertIn('RELATIONSHIP NOTE',message(d,1600))
            self.assertIn('up to 1% more',message(d,1900))
            self.assertEqual(message(d,2200),'')
