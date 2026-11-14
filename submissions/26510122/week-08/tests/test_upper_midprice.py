import unittest

from procurement_audit import select_lines
from stock_policy import replenishment_candidates


class UpperMidpriceTests(unittest.TestCase):
    now = 100_000
    catalog = {
        'upper': {'kind': 'goods', 'list_price': 270_000, 'category': 'upper', 'name': 'Upper'},
        'lower': {'kind': 'goods', 'list_price': 130_000, 'category': 'lower', 'name': 'Lower'},
    }

    def history(self):
        rows = []
        for sku, paid, cost in [('upper', 220_000, 120_000), ('lower', 90_000, 50_000)]:
            for index in range(2):
                unit = f'{sku}-{index}'
                rows += [{'event': 'receipt_signed', 'sku': sku, 'unit': unit, 'cost': cost,
                          'at': self.now - 120, 'supplied_at': self.now - 900},
                         {'event': 'settled', 'sku': sku, 'unit': unit, 'price': paid,
                          'at': self.now - 100, 'ledger': unit}]
        return rows

    def test_prioritizes_paid_upper_band_with_full_reservation_and_price_recheck(self):
        inv = {'round': 3, 'balance': 10_000_000, 'units': []}
        rows = replenishment_candidates(self.catalog, inv, self.history(), self.now)
        upper, lower = rows
        self.assertEqual([upper['sku'], lower['sku']], ['upper', 'lower'])
        self.assertGreater(upper['cost_ceiling_per_unit'], 300_000)
        self.assertEqual(upper['upper_midprice']['median_paid'], 220_000)
        self.assertGreater(upper['upper_midprice']['projected_contribution_per_unit'], 0)
        self.assertTrue(lower['midprice_priority'])
        self.assertIsNone(lower['upper_midprice'])
        inv['surge'] = [{'sku': 'upper', 'factor': 1.5}]
        rows = replenishment_candidates(self.catalog, inv, self.history(), self.now)
        self.assertEqual([r['sku'] for r in rows], ['lower'])

    def test_out_of_band_sales_do_not_build_multi_window_stock(self):
        history = []
        for sku, paid, cost in [('upper', 220_000, 120_000), ('lower', 90_000, 50_000)]:
            for index in range(10 if sku == 'upper' else 4):
                unit = f'{sku}-{index}'
                at = self.now - 120 - index * 60
                history += [{'event': 'receipt_signed', 'sku': sku, 'unit': unit, 'cost': cost,
                             'at': at, 'supplied_at': at - 900},
                            {'event': 'settled', 'sku': sku, 'unit': unit, 'price': paid,
                             'at': at, 'ledger': unit}]
        inventory = {'round': 3, 'balance': 10_000_000, 'units': []}
        upper, lower = replenishment_candidates(self.catalog, inventory, history, self.now)
        self.assertEqual([upper['sku'], lower['sku']], ['upper', 'lower'])
        self.assertTrue(any(o['scenario'].startswith('cover_') for o in upper['target_options']))
        self.assertFalse(any(o['scenario'].startswith('cover_') for o in lower['target_options']))
        self.assertGreater(upper['upper_midprice']['projected_contribution_12h'], 0)

    def test_nonviable_after_approval_does_not_emit_or_consume_budget(self):
        upper = {'sku': 'upper', 'kind': 'replenishment', 'category': 'upper',
                 'qty': 1, 'target_stock': 1, 'cost_ceiling': 303_750}
        lower = {'sku': 'lower', 'kind': 'replenishment', 'category': 'lower',
                 'qty': 1, 'target_stock': 1, 'cost_ceiling': 146_250}
        buys, audit = select_lines([upper, lower], stocks={}, categories={}, pending_stock={},
                                   catalog=self.catalog, unit_room=1, supply_lines=1, budget=400_000,
                                   allow_replenishment=True, allow_new_skus=True, time_limited=False,
                                   unprofitable_upper_midprice={'upper'})
        self.assertEqual(buys, [{'sku': 'lower', 'qty': 1}])
        self.assertEqual(audit[0]['reasons'], ['upper_midprice_margin_no_longer_positive'])


class MidpriceStockoutRecoveryTests(unittest.TestCase):
    now=UpperMidpriceTests.now
    catalog=UpperMidpriceTests.catalog
    history=UpperMidpriceTests.history
    def sparse(self):
        rows=self.history()
        rows=[e for e in rows if e['sku']=='upper']
        for e in rows:
            if e.get('unit')=='upper-0':e['at']=self.now-13*3600
        return rows

    def test_sparse_profitable_repeat_stockout_can_recover_only_one_unit(self):
        rows=replenishment_candidates(self.catalog,{'balance':10_000_000,'units':[]},
                                      self.sparse(),self.now)
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertTrue(row['midprice_stockout_recovery'])
        self.assertEqual({o['qty'] for o in row['target_options']},{1})
        self.assertEqual(row['upper_midprice']['evidence_status'],'limited_one_unit_recovery')
        self.assertGreater(row['cost_ceiling_per_unit'],300_000)

    def test_existing_stock_pending_or_new_loss_do_not_add_recovery_units(self):
        unit={'sku':'upper','kind':'goods','grade':'A','cost':120000,'category':'upper'}
        for inventory,pending,blocked in [
            ({'balance':10_000_000,'units':[unit]},None,()),
            ({'balance':10_000_000,'units':[]},{'upper':1},()),
            ({'balance':10_000_000,'units':[]},None,{'upper'}),
            ({'balance':10_000_000,'units':[],
              'surge':[{'sku':'upper','factor':1.5}]},None,())]:
            rows=replenishment_candidates(self.catalog,inventory,self.sparse(),self.now,
                                          pending=pending,blocked=blocked)
            self.assertFalse(any(r.get('midprice_stockout_recovery') for r in rows))
