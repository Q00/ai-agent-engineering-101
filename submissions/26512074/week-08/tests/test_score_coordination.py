import tempfile
import time
import unittest
from pathlib import Path
from score_coordination import snapshot_targets,procurement_value
from strategy_policy import warranty_rate,load,market_snapshot
from optimizer import atomic
from strategy_feedback import market_price


class CoordinationTests(unittest.TestCase):
    def test_targets_use_student_peers_instead_of_house_shops(self):
        with tempfile.TemporaryDirectory() as d:
            shops=[dict(name='totato2203-r3',book=100,revenue=100),dict(name='peer',book=150,revenue=300),dict(name='house',book=101,revenue=101)]
            board=dict(shops=shops,players=[dict(student=True,shop=dict(name=n)) for n in ['totato2203-r3','peer']])
            result=snapshot_targets(d,board,1000)
            self.assertEqual(result['targets']['book']['gap'],50)
            self.assertEqual(result['targets']['revenue']['gap'],200)

    def test_revenue_growth_can_win_when_book_gap_is_large(self):
        t=dict(at=1000,targets=dict(book=dict(gap=1000000),revenue=dict(gap=100)))
        a=dict(contribution=100,expected_net=100,expected_sale=110,need=1,hourly=1)
        b=dict(contribution=90,expected_net=90,expected_sale=1000,need=1,hourly=1)
        self.assertGreater(procurement_value(b,t,1000),procurement_value(a,t,1000))

    def test_stale_scoreboard_never_overrides_current_profit(self):
        c=dict(contribution=100)
        self.assertEqual(procurement_value(c,dict(at=0),1000),100)

    def test_warranty_after_six_hours_has_no_extra_breakage_risk(self):
        with tempfile.TemporaryDirectory() as d:
            p=load();p['warranty_safety']=0
            six=warranty_rate(d,'x','A',6,p)
            self.assertEqual(six,warranty_rate(d,'x','A',48,p))
            self.assertEqual(six,warranty_rate(d,'x','A',72,p))
            self.assertLess(warranty_rate(d,'x','A',3,p),six)

    def test_market_cache_observes_atomic_updates(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'strategy-market.json';atomic(path,dict(value=1))
            self.assertEqual(market_snapshot(d)['value'],1)
            atomic(path,dict(value=2));self.assertEqual(market_snapshot(d)['value'],2)

    def test_high_demand_protects_waiting_profit_without_blanket_markup(self):
        with tempfile.TemporaryDirectory() as d:
            now=time.time();unit=dict(unit='u',sku='x',kind='goods',grade='A',cost=50,list_price=100,supplied_at=now)
            flow=dict(trades=5,own_trades=1,market_rate=0,own_rate=0,prices={'A':dict(n=5,median=90,base_median=90)})
            atomic(Path(d)/'strategy-market.json',dict(at=now,skus={'x':flow}))
            low=market_price(d,unit,{},now)['floor']
            flow['own_rate']=4;atomic(Path(d)/'strategy-market.json',dict(at=now,skus={'x':flow}))
            high=market_price(d,unit,{},now)['floor']
            self.assertLess(low,high);self.assertLessEqual(high,90)

    def test_aged_goods_are_not_dumped_below_cost(self):
        with tempfile.TemporaryDirectory() as d:
            now=time.time();unit=dict(unit='u',sku='x',kind='goods',grade='A',cost=80,list_price=100,supplied_at=now-172800)
            atomic(Path(d)/'strategy-market.json',dict(at=now,skus={'x':dict(prices={'A':dict(n=5,median=70,base_median=70,q35=60)})}))
            self.assertGreaterEqual(market_price(d,unit,{},now)['floor'],80)
