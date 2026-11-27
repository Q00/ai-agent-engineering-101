"""Facade API and live monkeypatch compatibility; synthetic in-memory inputs only."""
import inspect
import unittest
from unittest.mock import patch

import seller_cash_policy as cash


NOW = 1_800_000_000
SKU = 'G00000001'


class CashModuleCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {SKU: {'kind':'goods', 'category':'beds', 'list_price':100000}}
        self.unit = {'sku':SKU, 'kind':'goods', 'grade':'A', 'cost':10000,
                     'supplied_at':NOW-60, 'cert':True}

    def test_original_public_signatures_and_function_modules(self):
        expected = {
            'allocate': '(groups, models, held, budget, cap, grade_spent, previous)',
            'availability_plan': '(units, hub, catalog, now, forum=None)',
            'compact_break_orders': '(hub, now, *, mature=False)',
            'compact_cash_sales': '(hub, now)', 'config': '()',
            'forecast': '(hub, sku, info, catalog, now, inquiries=0)',
            'note_stock': '(hub, held, now)', 'number': '(x)',
            'observe': '(hub, rows, catalog, now, own_urls, own_name)',
            'observe_break_stats': '(hub, rows, catalog, now, own_urls, own_name)',
            'procurement': '(hub, sku, info, inv, qty, catalog, now)',
            'profit_floor': '(unit, hub, catalog, now, warranty=None)',
            'quote': '(unit, hub, catalog, now)',
            'ready': '(hub, inv, now, inventory_fresh, phase)',
            'reference_price': "(hub, sku, grade, now, kind='goods', warranty=None)",
            'refund_probability': '(hub, sku, grade, catalog, now)',
            'sale_rows': '(hub)', 'spending_budget': '(hub, inv, existing_floor, now)',
            'stock_post': '(units, hub, catalog, now)',
            'warranty_hours': "(grade, kind='goods')",
        }
        actual = {name: str(inspect.signature(fn)) for name, fn in vars(cash).items()
                  if inspect.isfunction(fn) and fn.__module__ == cash.__name__}
        self.assertEqual(actual, expected)

    def test_quote_calls_patched_facade_functions(self):
        with patch.object(cash, 'config', return_value={'enabled':True}), \
                patch.object(cash, 'reference_price', return_value=20000) as reference, \
                patch.object(cash, 'warranty_hours', return_value=12) as warranty, \
                patch.object(cash, 'refund_probability', return_value=.2) as risk, \
                patch.object(cash, 'profit_floor', return_value=15000) as floor:
            result = cash.quote(self.unit, {}, self.catalog, NOW)
        self.assertEqual((result['open_ask'], result['r1_target'], result['hard_floor'],
                          result['break_rate'], result['warranty_hours']),
                         (20000, 19800, 15000, .2, 12))
        reference.assert_called_once_with({}, SKU, 'A', NOW, 'goods')
        warranty.assert_called_once_with('A', 'goods')
        risk.assert_called_once_with({}, SKU, 'A', self.catalog, NOW)
        floor.assert_called_once_with(self.unit, {}, self.catalog, NOW)

    def test_config_and_prior_replacements_reach_risk_and_floor(self):
        unit = dict(self.unit, grade='B')
        with patch.object(cash, 'config', return_value={'grade_warranty_hours':{'B':9}}), \
                patch.object(cash, 'PRIOR_BREAK', {'B':.4}):
            self.assertEqual(cash.warranty_hours('B'), 9)
            self.assertEqual(cash.refund_probability({}, SKU, 'B', self.catalog, NOW), .4)
            # cost + storage + contribution, with patched B risk minimum.
            self.assertEqual(cash.profit_floor(unit, {}, self.catalog, NOW), 17501)

    def test_reference_price_uses_patched_warranty_and_numeric_validator(self):
        hub = {'demand_radar':{'sku_comparables':{SKU:[
            {'grade':'A', 'ts':NOW, 'amount':12345, 'warranty_hours':0}]*5}}}
        with patch.object(cash, 'warranty_hours', return_value=0):
            self.assertEqual(cash.reference_price(hub, SKU, 'A', NOW), 12345)
            with patch.object(cash, 'number', return_value=False):
                self.assertIsNone(cash.reference_price(hub, SKU, 'A', NOW))

    def test_procurement_calls_patched_pricing_and_risk(self):
        with patch.object(cash, 'reference_price', return_value=100000) as reference, \
                patch.object(cash, 'refund_probability', return_value=.2) as risk, \
                patch.object(cash, 'warranty_hours', return_value=6), \
                patch.object(cash, 'PRIOR_BREAK', {'B':.3}):
            result = cash.procurement({}, SKU, self.catalog[SKU], {}, 1, self.catalog, NOW)
        self.assertEqual(result['break_rates'], {'A':.2, 'B':.3, 'C':.2})
        # Unknown surge units retain the existing current + qty*.002 estimate.
        self.assertEqual(result['projected_surge'], 1.002)
        self.assertEqual(result['grade_contributions'], {'A':14870., 'B':19900., 'C':44930.})
        self.assertEqual(reference.call_count, 3)
        self.assertEqual(risk.call_count, 3)

    def test_forecast_uses_live_configuration(self):
        with patch.object(cash, 'config', return_value={'maximum_depth':2}):
            self.assertEqual(cash.forecast({}, SKU, self.catalog[SKU], self.catalog, NOW)['depth'], 2)

    def test_budget_uses_live_prior_and_configuration(self):
        hub = {'seller_cash':{'sales':{'1':{'own':True, 'ts':NOW, 'grade':'A',
                                          'amount':10000, 'warranty_hours':6, 'order':'o'}}}}
        with patch.object(cash, 'config', return_value={'minimum_cash_reserve':0, 'deployable_fraction':.5}), \
                patch.object(cash, 'PRIOR_BREAK', {'A':.5}):
            self.assertEqual(cash.spending_budget(hub, {'balance':20000}, 0, NOW), (5000, 10000))

    def test_forum_uses_live_configuration_and_validator(self):
        hub = {'forum_state':{'last_shop_post_ts':NOW-400},
               'demand_radar':{'sku_inquiries':{SKU.lower():[{'ts':NOW}]}}}
        with patch.object(cash, 'config', return_value={'forum_interval_seconds':300}):
            self.assertIsNotNone(cash.availability_plan([self.unit], hub, self.catalog, NOW))
            with patch.object(cash, 'number', return_value=False):
                self.assertIsNone(cash.availability_plan([self.unit], hub, self.catalog, NOW))

    def test_stock_post_still_calls_patched_availability_plan(self):
        with patch.object(cash, 'availability_plan', return_value={'action':{'post':'patched'}}):
            self.assertEqual(cash.stock_post([], {}, {}, NOW), 'patched')

    def test_observation_keeps_existing_patch_boundary(self):
        with patch.object(cash, 'config', return_value={}), \
                patch.object(cash, 'observe_break_stats') as observe, \
                patch.object(cash, 'compact_cash_sales') as compact:
            hub = {}
            cash.observe(hub, [], self.catalog, NOW, set(), 'own')
        observe.assert_called_once_with(hub, [], self.catalog, NOW, set(), 'own')
        compact.assert_called_once_with(hub, NOW)


if __name__ == '__main__':
    unittest.main()
