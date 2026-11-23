import tempfile,unittest
from pathlib import Path
from adaptive_capital import allocation
from profit_strategy import settings,capital


class AdaptiveCapitalTests(unittest.TestCase):
    def test_growth_requires_new_profit_evidence_and_reserves_remain(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d);p=dict(settings(),inventory_equity_fraction=.7,normal_inventory_fraction=.6,stock_cover_hours=2,verified_restock_fraction=.8)
            allocation(md,dict(balance=10000000,units=[]),{},p,0)
            metrics=dict(mature_sales=6,known_cost_sales=6,roi=.2,net_after_refunds_storage=100000,
                         refund_rate=0,defect_rate=0,ledger_age_seconds=0,median_turnover_hours=1,
                         stale_high_units=0,latest_evidence_at=3500)
            inv=dict(balance=12000000,units=[])
            effective,state=allocation(md,inv,dict(metrics=metrics),p,3600)
            self.assertEqual(state['stage'],1)
            self.assertGreater(effective['inventory_equity_fraction'],p['inventory_equity_fraction'])
            self.assertEqual(capital(inv,4000000,effective)['cash_reserve'],capital(inv,4000000,p)['cash_reserve'])
            self.assertEqual(allocation(md,inv,dict(metrics=metrics),p,7200)[1]['stage'],1)
            inv['balance']=10000000
            self.assertEqual(allocation(md,inv,dict(metrics=metrics),p,10800)[1]['stage'],0)

    def test_asset_growth_alone_does_not_expand(self):
        with tempfile.TemporaryDirectory() as d:
            md=Path(d);p=settings()
            allocation(md,dict(balance=10000000,units=[]),{},p,0)
            self.assertEqual(allocation(md,dict(balance=20000000,units=[]),{},p,7200)[1]['stage'],0)
