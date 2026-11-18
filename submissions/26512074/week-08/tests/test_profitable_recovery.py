import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from optimizer import db
from profit_strategy import replenish


class ProfitableRecoveryTests(unittest.TestCase):
    def plan(self,net,old_stock=False):
        with tempfile.TemporaryDirectory() as d:
            now=100000
            units=[dict(unit='old',sku='x',cost=100000,kind='goods',supplied_at=1)] if old_stock else []
            events=[dict(seq=i,kind='settle',round=3,ts=now-100,
                         data=dict(sku='x',shop='totato2203-r3',amount=160000)) for i in range(3)]
            with db(d) as c:
                for i in range(3):
                    c.execute('insert into units values(?,?,?,?)',(str(i),'x',100000,now-1000))
                    c.execute('insert into trials(task,sku,at,cost,settled_at,price) values(?,?,?,?,?,?)',
                              (str(i),'x',now-200,100000,now-100,160000))
            growth=dict(stage=0,share=0,paused_until=now+3600)
            stats=dict(sku_net=dict(x=dict(sales=5,net=net)),reserve_rate=.05,measured_rate=.02,mature_warranted_sales=100)
            with patch('high_value_growth.update',return_value=growth),patch('warranty_risk.statistics',return_value=stats):
                result=replenish(dict(balance=6000000,units=units,restock=dict(open=True)),
                                 {'x':dict(kind='goods',list_price=200000)},events,now,d)
            preview=json.loads(Path(d,'procurement-preview.json').read_text())
            return result,preview

    def test_profitable_paid_sku_replenishes_during_unrelated_global_pause(self):
        result,preview=self.plan(500000)
        self.assertTrue(result['buy'])
        self.assertLessEqual(result['buy'][0]['qty'],2)
        self.assertTrue(preview['candidates'][0]['profitable_recovery'])

    def test_losing_sku_remains_blocked(self):
        self.assertEqual(self.plan(-100)[0]['buy'],[])

    def test_old_unsold_units_do_not_receive_recovery_allocation(self):
        self.assertEqual(self.plan(500000,True)[0]['buy'],[])
