import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import seller_r3


class StaleUnitSelectionTests(unittest.TestCase):
    def select(self,text):
        with tempfile.TemporaryDirectory() as d:
            units=[dict(unit='a',sku='G12345678',name='Sofa',category='sofa',kind='goods',grade='A',cost=1200000,list_price=1800000,supplied_at=1000,expires_at=None),
                   dict(unit='c',sku='G12345678',name='Sofa',category='sofa',kind='goods',grade='C',cost=600000,list_price=1800000,supplied_at=2000,expires_at=None)]
            Path(d,'inventory.json').write_text(json.dumps(dict(round=3,units=units)))
            with patch.object(seller_r3,'MARKET_DIR',Path(d)),patch.object(seller_r3.time,'time',return_value=50000):
                return seller_r3.find_unit(text)

    def test_stale_expensive_unit_does_not_hide_cheaper_grade(self):
        self.assertEqual(self.select('G12345678 quote total')['unit'],'c')

    def test_explicit_grade_is_preserved(self):
        self.assertEqual(self.select('G12345678 grade A')['unit'],'a')
        self.assertEqual(self.select('G12345678 A등급')['unit'],'a')

    def test_stale_sku_without_recent_sale_is_not_replenished(self):
        from profit_strategy import replenish
        with tempfile.TemporaryDirectory() as d:
            inv=dict(balance=6000000,restock=dict(open=True),units=[dict(unit='u',sku='x',kind='goods',grade='A',cost=100000,supplied_at=1000)])
            events=[dict(seq=i,kind='settle',round=3,ts=49999,data=dict(sku='x',shop=f's{i%3}',buyer=f'b{i%3}',amount=150000)) for i in range(12)]
            result=replenish(inv,{'x':dict(list_price=200000,kind='goods')},events,50000,d)
            self.assertEqual(result['buy'],[])
            preview=json.loads(Path(d,'procurement-preview.json').read_text())
            self.assertTrue(any(r['reason']=='stale_inventory_without_recent_sale' for r in preview['excluded']))
