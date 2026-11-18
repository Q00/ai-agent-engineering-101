import unittest
from sku_evidence import evaluate


class SkuEvidenceTests(unittest.TestCase):
    def fixture(self):
        sku='Gb5d0dde7';events=[]
        for i in range(10):
            seller=f's{i%3}'
            events.extend([dict(seq=i*2,round=3,kind='supply',ts=8000,data=dict(shop=seller,sku=sku,total=100,units=[f'u{i}'])),
                           dict(seq=i*2+1,round=3,kind='settle',ts=8100+i,data=dict(shop=seller,buyer=f'b{i%4}',sku=sku,unit=f'u{i}',amount=130,grade='A'))])
        return sku,events

    def test_positive_mature_evidence_not_just_revenue(self):
        sku,events=self.fixture()
        r=evaluate(events+events,{sku:dict(kind='goods')},10000)['skus'][sku]
        self.assertTrue(r['eligible'])
        self.assertEqual(r['exact_mature_sales'],10)
        for e in events:
            if e['kind']=='settle':e['data']['amount']=90
        self.assertFalse(evaluate(events,{sku:dict(kind='goods')},10000)['skus'][sku]['eligible'])

    def test_unmatured_warranty_and_batch_costs_are_not_exact_evidence(self):
        sku,events=self.fixture()
        for e in events:
            if e['kind']=='settle':e['data']['warranty_hours']=48
        self.assertEqual(evaluate(events,{sku:dict(kind='goods')},10000)['skus'][sku]['exact_mature_sales'],0)

    def test_large_unsold_exposure_blocks_successful_sale_selection_bias(self):
        sku,events=self.fixture()
        events.append(dict(seq=100,round=3,kind='supply',ts=1000,data=dict(shop='s',sku=sku,total=10000,units=['stale'])))
        self.assertFalse(evaluate(events,{sku:dict(kind='goods')},10000)['skus'][sku]['eligible'])
