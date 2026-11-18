import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from premium import terms,WARRANTY_FEES
from after_sales import warranty_offer
from buyer_compare import quoted_warranty
from bundle_offer import discount,FIRST_PROMO
from recommendations import database,measure_feedback


class WarrantyOptionsTests(unittest.TestCase):
    def test_all_terms_and_funding_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            for h,fee in WARRANTY_FEES.items():
                result=terms(directory,str(h),dict(sku='x'),h,flexible=False)
                self.assertEqual(result,dict(hours=h,fee=fee))
                self.assertEqual(quoted_warranty(f'warranty {h} hours'),h)
            self.assertTrue(warranty_offer(directory,dict(kind='goods',unit='x',sku='x',grade='A'),100,1000000,1000,48))
            holds=json.loads((Path(directory)/'warranty-holds.json').read_text())
            self.assertEqual(holds['x']['expires_at'],1000+48*3600+900)

    def test_first_discount_stacks_with_cross_sku_discount(self):
        ref='00000000-0000-0000-0000-000000000001'
        events=[dict(seq=1,kind='settle',round=3,ts=1001,data=dict(shop='ours',order='o')),
                dict(seq=2,kind='result',round=3,ts=1002,data=dict(order='o',buyer='b',verified=True,receipt=dict(task=ref,item='old')))]
        with patch('bundle_offer.ownership_tags',return_value={'ours'}),patch('round3.read',return_value=[dict(author='ours',ts=1000,text=FIRST_PROMO)]):
            result=discount(ref,'new',1000,900,events,1003)
        self.assertEqual(result['rate'],.02)
        self.assertEqual(result['price'],980)

    def test_own_only_publication_does_not_crash_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            c=database(Path(directory))
            try:
                c.execute('insert into publications values(?,?,?,?,?,?,null)',('d',1000,'own-featured','s','body',json.dumps(dict(sku='own-featured',shop='ours',own=True))))
                measure_feedback(c,[],[],[],1001)
            finally:c.close()
