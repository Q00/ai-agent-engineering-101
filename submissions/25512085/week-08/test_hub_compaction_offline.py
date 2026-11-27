"""Exact consumer comparisons and isolated large-state migration, no network."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import market_buyer
import seller_cash_policy as cash
import store

NOW = 1793800000
SKU = 'G00000001'
FOOD = 'f00000002'


def byte_size(hub):
    return len(json.dumps(hub, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


class HubCompactionTests(unittest.TestCase):
    def fixture(self):
        hub = store.default_hub()
        catalog = {SKU: {'kind':'goods', 'category':'goods', 'list_price':100000},
                   FOOD: {'kind':'food', 'category':'food', 'list_price':100000}}
        for sku in catalog:
            hub['demand_radar']['sku_comparables'][sku] = [
                {'ts':NOW-age, 'amount':10000+i*137, 'grade':grade,
                 'warranty_hours':warranty, 'expires_at':NOW-age+100*3600,
                 'shop':'unused', 'order':'unused', 'seq':i, 'shelf_hours':100}
                for i, (age, grade, warranty) in enumerate(
                    [(i*90, 'A', 72) for i in range(19)] +
                    [(4000+i*90, 'C', 0) for i in range(19)] +
                    [(10800, 'C', 0), (10801, 'C', 0)])]
        hub['seller_cash'] = {'sales':{str(i):{
            'sku':SKU if i % 3 else FOOD, 'ts':NOW-age,
            'amount':10000+i, 'grade':'A', 'warranty_hours':72,
            'order':str(i), 'own':i % 4 == 0}
            for i, age in enumerate(list(range(0, 5000, 80)) + [3600, 3601, 21600, 28800])},
            'breaks':{'0':{'ts':NOW, 'refunded':10000}}}
        # Migration must preserve existing counters, including not-yet-matured
        # and already counted sales, rather than count sales twice.
        hub['break_stats'] = {SKU:{'A':{'sold':23, 'broken':2, 'last_ts':NOW-100}}}
        hub['market_break_stats'] = {SKU:{'A':{'sold':91, 'broken':7, 'last_ts':NOW-100}}}
        hub['break_observer'] = {'seeded':True, 'seq':100, 'orders':{
            'own':{'sku':SKU, 'grade':'A', 'ts':NOW-100, 'own':True, 'sold':False, 'broken':False},
            'market':{'sku':SKU, 'grade':'A', 'ts':NOW-100, 'own':False, 'sold':False, 'broken':False},
            'mature':{'sku':SKU, 'grade':'A', 'ts':NOW-7*3600, 'own':False, 'sold':True, 'broken':True}},
            'unmatched':{'late':NOW-100, 'expired':NOW-9*3600}}
        hub['demand_radar']['sku_settles'][SKU] = [100, 200, 300]
        return hub, catalog

    def consumer_results(self, hub, catalog, now):
        return {
            'reference':[cash.reference_price(hub, sku, grade, now, info['kind'])
                         for sku, info in catalog.items() for grade in 'ABC'],
            'forecast':[cash.forecast(hub, sku, info, catalog, now, 7)
                        for sku, info in catalog.items()],
            'refund':[cash.refund_probability(hub, SKU, g, catalog, now) for g in 'ABC'],
            'budget':cash.spending_budget(hub, {'balance':10000000}, 100000, now),
            'buyer':market_buyer.food_price_policy(catalog, hub, FOOD, 100000,
                                                   {'use_within_hours':1},
                                                   {'claim':'A', 'expires_at':now+100*3600}, now),
            'anchor':store.get_sku_market_anchor(hub, SKU, 100000),
            'post':cash.stock_post([{'sku':SKU, 'kind':'goods', 'grade':'A', 'cert':'cert'}],
                                  hub, catalog, now),
            'signals':{r['sku'] for r in cash.sale_rows(hub) if 0 <= now-r['ts'] <= 3600},
        }

    def test_consumers_exact_at_boundaries_and_later(self):
        original, catalog = self.fixture()
        compact = copy.deepcopy(original)
        store.compact_hub(compact, NOW)
        for delta in (0, 1, 60, 1200, 3600):
            self.assertEqual(self.consumer_results(original, catalog, NOW+delta),
                             self.consumer_results(compact, catalog, NOW+delta))
        self.assertEqual(len(compact['demand_radar']['sku_comparables'][SKU]), 39)
        self.assertEqual(set(compact['break_observer']['orders']), {'own'})
        self.assertEqual(set(compact['break_observer']['market_orders']), {'market', 'mature'})
        self.assertNotIn('expired', compact['break_observer']['unmatched'])
        self.assertTrue(all(r['own'] for r in compact['seller_cash']['sales'].values()))

    def test_observer_legacy_and_compact_follow_same_events(self):
        original, catalog = self.fixture()
        compact = copy.deepcopy(original)
        store.compact_hub(compact, NOW)
        rows = [{'seq':101+i, 'round':3, 'kind':'broken', 'ts':NOW+1,
                 'data':{'order':order}} for i, order in enumerate(('market', 'own', 'mature', 'late'))]
        for now in (NOW+1, NOW+6*3600, NOW+8*3600+1):
            cash.observe(original, rows, catalog, now, {'own'}, 'mjforge')
            cash.observe(compact, rows, catalog, now, {'own'}, 'mjforge')
            for key in ('break_stats', 'market_break_stats'):
                self.assertEqual(original[key], compact[key])
            self.assertEqual(self.consumer_results(original, catalog, now),
                             self.consumer_results(compact, catalog, now))
        self.assertEqual(compact['market_break_stats'][SKU]['A']['sold'], 92)
        self.assertEqual(compact['market_break_stats'][SKU]['A']['broken'], 8)
        self.assertEqual(compact['break_stats'][SKU]['A']['sold'], 24)
        self.assertEqual(compact['break_stats'][SKU]['A']['broken'], 3)

    def test_seeds_survive_migration_and_remain_idempotent(self):
        hub = store.default_hub()
        catalog = {SKU:{'kind':'goods'}}
        cash.observe(hub, [], catalog, NOW, {'own'}, 'mjforge')
        stats = copy.deepcopy(hub['break_stats'])
        store.compact_hub(hub, NOW)
        cash.observe(hub, [], catalog, NOW+1, {'own'}, 'mjforge')
        self.assertEqual(stats, hub['break_stats'])
        self.assertEqual(hub['break_stats']['G1515d951']['A']['sold'], 110)

    def test_market_sales_replay_and_own_backfill(self):
        hub, catalog = self.fixture()
        rows = [{'seq':200+i, 'round':3, 'kind':'settle', 'ts':NOW-age,
                 'data':{'order':str(i), 'sku':SKU, 'amount':10000,
                         'grade':'A', 'shop':'other', 'buyer':'outside'}}
                for i, age in enumerate((0, 3600, 3601))]
        cash.observe(hub, rows, catalog, NOW, {'own'}, 'mjforge')
        before = cash.forecast(hub, SKU, catalog[SKU], catalog, NOW)
        cash.observe(hub, rows, catalog, NOW, {'own'}, 'mjforge')
        self.assertEqual(before, cash.forecast(hub, SKU, catalog[SKU], catalog, NOW))
        # Identity discovery backfills own sales independently of the break cursor.
        cash.observe(hub, rows, catalog, NOW, {'other'}, 'mjforge')
        self.assertTrue(hub['seller_cash']['sales']['200']['own'])
        self.assertEqual(before['market60'], cash.forecast(hub, SKU, catalog[SKU], catalog, NOW)['market60'])

    def test_first_load_migrates_once_and_sweeps_are_throttled(self):
        hub, _ = self.fixture()
        with tempfile.TemporaryDirectory(prefix='hub-compaction-') as tmp:
            path = Path(tmp) / 'hub.json'
            path.write_text(json.dumps(hub), encoding='utf-8')
            with patch.object(store.time, 'time', return_value=NOW), \
                    patch.object(store, 'compact_hub', wraps=store.compact_hub) as compact:
                loaded = store.load_hub(path)
                first = path.read_bytes()
                store.load_hub(path)
                self.assertEqual(first, path.read_bytes())
                for _ in range(3):
                    with store.mutate_hub(path):
                        pass
                self.assertEqual(compact.call_count, 1)
                self.assertEqual(loaded['hub_compaction']['version'], 1)
            with patch.object(store.time, 'time', return_value=NOW+60), \
                    patch.object(store, 'compact_hub', wraps=store.compact_hub) as compact:
                with store.mutate_hub(path):
                    pass
                self.assertEqual(compact.call_count, 1)

    def test_large_synthetic_state_size(self):
        hub = store.default_hub()
        # Measured cardinalities; timestamps spread uniformly over eight hours.
        # This is a reproducible synthetic case, not a claim about VM contents.
        for i in range(1314):
            hub['demand_radar']['sku_comparables'][f'G{i:08x}'] = [
                {'ts':NOW-j*600, 'amount':10000+j, 'grade':'ABC'[j%3],
                 'expires_at':None, 'shelf_hours':None, 'shop':'https://other.example/shop',
                 'order':f'order-{i}-{j}', 'seq':i*50+j, 'warranty_hours':6}
                for j in range(50)]
        hub['seller_cash'] = {'sales':{str(i):{
            'sku':f'G{i%1314:08x}', 'ts':NOW-i*28800//19807, 'amount':10000,
            'grade':'A', 'warranty_hours':6, 'order':f'order-{i}', 'own':i%500==0}
            for i in range(19807)}}
        hub['break_observer'] = {'orders':{f'order-{i}':{
            'sku':f'G{i%1314:08x}', 'grade':'A', 'ts':NOW-i*21600//13949,
            'own':i%500==0, 'sold':False, 'broken':False} for i in range(13949)}}
        hub['other_unchanged'] = 'x'*800000
        before = byte_size(hub)
        store.compact_hub(hub, NOW)
        after = byte_size(hub)
        print(f'\nSynthetic hub bytes: {before} -> {after}; '
              f'comparables={byte_size(hub["demand_radar"]["sku_comparables"])}; '
              f'sales={byte_size(hub["seller_cash"])}; '
              f'breaks={byte_size(hub["break_observer"])}')
        self.assertLess(after, before*.45)
        self.assertLess(after, 3200000)


if __name__ == '__main__':
    unittest.main()
