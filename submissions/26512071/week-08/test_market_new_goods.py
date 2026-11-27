"""market-new-goods-v1: NEW goods SKUs admitted from fresh, deduplicated, independent public paid settlements
(>= 40 exact order+unit+seq settles, >= 7 non-own shops, >= 3 buyers in the last 2 h of a fresh gap-free cache),
ranked BEFORE the old own-proven candidates so the greedy room waterfall can actually size an exploration tranche.
Held/requested SKUs with zero own paid sales are never re-bought from this tier. Offline fixtures only."""
import json
import tempfile
import unittest
from pathlib import Path

import microeconomics
import oneday_pilot

HERE = Path(__file__).resolve().parent
NOW = 1794649600.0
OWN = 'https://own.example'
FIX_CACHE = HERE/'fixture_public_settlements_20261114_newgoods.json'
FIX_CATALOG = HERE/'fixture_catalog_20261114_newgoods.json'
FIX_INV = HERE/'fixture_inventory_20261114_newgoods.json'
SPRAY = 'G630a202e'


def settle(seq, sku, shop, buyer, amount=380000, ts=NOW-600, grade='A', warranty=None, **over):
    data = {'shop':shop, 'item':sku, 'sku':sku, 'amount':amount, 'grade':grade, 'expires_at':None,
            'warranty_hours':warranty, 'unit':f'u-{seq}', 'order':f'o-{seq}', 'buyer':buyer, 'claim':grade,
            'claim_honest':True}
    data.update(over)
    return {'seq':seq, 'ts':ts, 'kind':'settle', 'round':3, 'data':data}


def market(sku, n=40, shops=7, buyers=3, first=1000, **kw):
    return [settle(first+i, sku, f'https://s{i % shops}.example', f'b{i % buyers}', **kw) for i in range(n)]


CATALOG = {'NEW1':{'kind':'goods', 'list_price':545930, 'category':'sofa'},
           'NEW2':{'kind':'goods', 'list_price':122900, 'category':'bed'},
           'FOOD':{'kind':'food', 'list_price':30000, 'category':'food', 'shelf_hours':200},
           'OLD':{'kind':'goods', 'list_price':3860, 'category':'spray'}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = Path(self.tmp.name)
        self.inv = {'round':3, 'units':[]}
        self.led = {'pilot':'one-day-v1', 'requests':{}, 'units':{}, 'events':{}, 'conflicts':{}}
        self.evidence = {'own_sku_ids':{}, 'own_ledger':{}}

    def tearDown(self):
        self.tmp.cleanup()

    def cache(self, rows, fetched=NOW-30, gaps=None, stale=False):
        (self.p/'public-settlements.json').write_text(json.dumps(
            {'source_url':'https://market.ourolabs.ai/api/ledger?after=-500', 'fetched_at':fetched,
             'own_urls':[OWN], 'rows':[], 'price_rows':rows, 'stale':stale, 'cache_version':5,
             'gaps':gaps or []}), encoding='utf-8')

    def run_new(self, exclude=(), catalog=CATALOG, now=NOW):
        return microeconomics.new_goods(self.p, catalog, self.inv, self.evidence, self.led, set(exclude), now)


class Gate(Base):
    def test_qualifies_at_thresholds_with_conservative_upper_bound(self):
        self.cache(market('NEW1'))
        cands, table, summary = self.run_new()
        self.assertEqual([c[0] for c in cands], ['NEW1'])
        self.assertEqual(cands[0][1], oneday_pilot.upper_bound(545930))
        m = table[0]['market']
        self.assertEqual((m['settlements'], m['shops'], m['buyers']), (40, 7, 3))
        self.assertEqual(len(m['ledger_ids']), 40)
        self.assertEqual(table[0]['tier'], microeconomics.NEW_GOODS_TIER)
        self.assertIn('not', m['label'])  # labelled: not a forecast / guaranteed margin
        self.assertEqual(summary['qualified'], 1)

    def test_dedup_seq_order_unit_before_count(self):
        rows = market('NEW1', n=39)
        rows += [dict(rows[0])]  # duplicate seq
        rows += [settle(5000, 'NEW1', 'https://s1.example', 'b1', order='o-1000')]  # duplicate order
        rows += [settle(5001, 'NEW1', 'https://s2.example', 'b2', unit='u-1001')]  # duplicate unit
        self.cache(rows)
        self.assertEqual(self.run_new()[0], [])
        self.cache(market('NEW1', n=39)+[settle(5002, 'NEW1', 'https://s3.example', 'b0')])
        self.assertEqual([c[0] for c in self.run_new()[0]], ['NEW1'])

    def test_shop_and_buyer_diversity_and_own_shop_excluded(self):
        self.cache(market('NEW1', shops=6))
        self.assertEqual(self.run_new()[0], [])
        self.cache(market('NEW1', buyers=2))
        self.assertEqual(self.run_new()[0], [])
        rows = market('NEW1', n=39)+[settle(9000, 'NEW1', OWN, 'b9')]
        self.cache(rows)
        self.assertEqual(self.run_new()[0], [])

    def test_null_warranty_stays_null_never_zero(self):
        self.cache(market('NEW1', n=20)+market('NEW1', n=20, first=3000, warranty=72.0))
        m = self.run_new()[1][0]['market']
        self.assertEqual(m['warranty_hours_observed'], {'null':20, '72.0':20})
        self.assertNotIn('0', m['warranty_hours_observed'])

    def test_stale_future_malformed_rows_not_counted(self):
        good = market('NEW1', n=39)
        mismatch = settle(7007, 'NEW1', 'https://s1.example', 'b1')
        mismatch['data']['sku'] = 'OTHER'
        bad = [settle(7000, 'NEW1', 'https://s1.example', 'b1', ts=NOW-7300),  # older than 2 h
               settle(7001, 'NEW1', 'https://s1.example', 'b1', ts=NOW+60),  # future
               settle(7002, 'NEW1', 'https://s1.example', 'b1', grade='D'),
               settle(7003, 'NEW1', 'https://s1.example', 'b1', claim_honest=False),
               settle(7004, 'NEW1', 'https://s1.example', 'b1', order=None),
               settle(7005, 'NEW1', 'https://s1.example', 'b1', amount=float('nan')),
               settle(7006, 'NEW1', 'https://s1.example', 'b1', expires_at=NOW+3600),
               mismatch]
        self.cache(good+bad)
        cands, _, summary = self.run_new()
        self.assertEqual(cands, [])
        self.assertGreaterEqual(summary['rows_rejected'], 6)

    def test_stale_cache_or_any_gap_blocks_everything(self):
        self.cache(market('NEW1'), fetched=NOW-1801)
        self.assertEqual(self.run_new()[0], [])
        self.cache(market('NEW1'), gaps=[{'lo':1, 'hi':5, 'detected_at':NOW-100}])
        cands, _, summary = self.run_new()
        self.assertEqual(cands, [])
        self.assertIn('gap', summary['reason'])
        self.cache(market('NEW1'), stale=True)
        self.assertEqual(self.run_new()[0], [])

    def test_catalog_goods_positive_finite_only_and_not_already_old(self):
        self.cache(market('FOOD')+market('MISSING', first=3000)+market('NEW1', first=5000))
        cat = dict(CATALOG, NEW1={'kind':'goods', 'list_price':0})
        self.assertEqual(self.run_new(catalog=cat)[0], [])
        self.assertEqual(self.run_new(exclude={'NEW1'})[0], [])

    def test_held_or_requested_with_zero_own_paid_never_rebought(self):
        self.cache(market('NEW1'))
        self.inv['units'] = [{'unit':'u-h', 'sku':'NEW1', 'cost':300000, 'kind':'goods'}]
        cands, table, _ = self.run_new()
        self.assertEqual(cands, [])
        self.assertEqual(table[0]['tier'], 'held_unsold_no_realized_sale')
        self.assertFalse(table[0]['buy_candidate'])
        self.inv['units'] = []
        self.led['requests'] = {'x:0':{'sku':'NEW1', 'qty':2, 'status':None}}
        self.assertEqual(self.run_new()[0], [])
        self.led['requests'] = {'x:0':{'sku':'NEW1', 'qty':2, 'status':'refused'}}  # released: nothing held
        self.assertEqual([c[0] for c in self.run_new()[0]], ['NEW1'])
        self.inv['units'] = [{'unit':'u-h', 'sku':'NEW1', 'cost':300000, 'kind':'goods'}]
        self.evidence['own_ledger'] = {'77':{'sku':'NEW1', 'price':390000}}  # exact own paid promotion
        self.assertEqual([c[0] for c in self.run_new()[0]], ['NEW1'])

    def test_rank_paid_then_shops_then_sku_deterministic(self):
        rows = market('NEW2', n=45, shops=10, amount=90000)+market('NEW1', n=45, shops=8, first=3000)
        self.cache(rows)
        a = [c[0] for c in self.run_new()[0]]
        self.cache(list(reversed(rows)))
        self.assertEqual(a, ['NEW2', 'NEW1'])
        self.assertEqual([c[0] for c in self.run_new()[0]], a)
        self.cache(market('NEW2', n=41, shops=10, amount=90000)+market('NEW1', n=50, shops=7, first=3000))
        self.assertEqual([c[0] for c in self.run_new()[0]], ['NEW1', 'NEW2'])


class Integration(Base):
    def test_evaluate_puts_new_market_before_old_ranked_candidates(self):
        self.cache(market('NEW1'))
        (self.p/'one-day-pilot-ledger.json').write_text(json.dumps(self.led), encoding='utf-8')
        catalog = dict(CATALOG)
        evidence = {'own_sku_ids':{'OLD':[1, 2]}, 'own_ledger':{'1':{'sku':'OLD', 'price':3500},
                                                                '2':{'sku':'OLD', 'price':3600}}}
        base, ranked, table, facts, err = microeconomics.evaluate(self.p, self.inv, catalog, evidence, 1, NOW)
        self.assertIsNone(err)
        self.assertEqual([c[0] for c in base], ['OLD'])
        self.assertEqual([c[0] for c in ranked], ['NEW1', 'OLD'])
        self.assertEqual(table[0]['sku'], 'NEW1')
        self.assertEqual(facts['new_goods_summary']['qualified'], 1)

    def test_new_goods_error_keeps_old_ranking(self):
        (self.p/'one-day-pilot-ledger.json').write_text(json.dumps(self.led), encoding='utf-8')
        (self.p/'public-settlements.json').write_text('{not json', encoding='utf-8')
        evidence = {'own_sku_ids':{'OLD':[1, 2]}, 'own_ledger':{'1':{'sku':'OLD', 'price':3500},
                                                                '2':{'sku':'OLD', 'price':3600}}}
        _, ranked, _, facts, err = microeconomics.evaluate(self.p, self.inv, CATALOG, evidence, 1, NOW)
        self.assertIsNone(err)
        self.assertEqual([c[0] for c in ranked], ['OLD'])
        self.assertEqual(facts['new_goods_summary']['qualified'], 0)

    def test_room_sizes_two_couches_then_old_fills_remainder(self):
        """restock_r3._oneday_plan sizing arithmetic (qty = min(room // ub, PER_CALL)), unchanged."""
        import restock_r3
        room, out = 1290000, []
        for sku, ub in [('G021aae64', oneday_pilot.upper_bound(545930)), (SPRAY, oneday_pilot.upper_bound(3860))]:
            q = max(0, min(int(room//ub), restock_r3.PER_CALL))
            out.append((sku, q))
            room -= q*ub
        self.assertEqual(out[0], ('G021aae64', 2))
        self.assertGreater(out[1][1], 0)


class FrozenReal(Base):
    """Real cache/catalog/inventory captured 2026-11-14 (own fixture paths)."""

    def test_real_snapshot_winner_and_spray_excluded(self):
        cache = json.loads(FIX_CACHE.read_text(encoding='utf-8'))
        (self.p/'public-settlements.json').write_text(json.dumps(cache), encoding='utf-8')
        catalog = json.loads(FIX_CATALOG.read_text(encoding='utf-8'))
        inv = json.loads(FIX_INV.read_text(encoding='utf-8'))
        self.inv = inv
        cands, table, summary = self.run_new(catalog=catalog, now=cache['fetched_at']+5)
        names = [c[0] for c in cands]
        self.assertTrue(names)
        self.assertEqual(names[0], 'G021aae64')
        self.assertNotIn(SPRAY, names)
        top = table[0]['market']
        self.assertGreaterEqual(top['settlements'], 40)
        self.assertGreaterEqual(top['shops'], 7)
        self.assertGreaterEqual(top['buyers'], 3)
        self.assertIn('null', top['warranty_hours_observed'])


if __name__ == '__main__':
    unittest.main()
