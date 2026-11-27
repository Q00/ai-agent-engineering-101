"""Kind correction: synthetic fixtures only, no runtime or policy input files."""
import copy
from itertools import permutations
import json
from pathlib import Path
import tempfile
import unittest

import buyer_outcomes as b
from test_buyer_outcomes_offline import OWN, row


def sale(kind='food', sku='G12345678', **meta):
    return b.normalize([row(item=sku, amount=85, warranty_hours=None)],
                       {sku: dict(list_price=100, kind=kind, **meta)}, OWN, 200)[0][0]


def legacy():
    e = sale()
    for key in ('item_kind', 'item_kind_source', 'catalog_kind', 'catalog_kind_state',
                'catalog_kind_observed_at', 'ledger_item_kind', 'ledger_kind_state'):
        e.pop(key, None)
    e.update(version=1, goods=True)
    return e


def summary(events):
    return b.summarize(events, OWN, 30000)


class KindTests(unittest.TestCase):
    def test_gprefix_food_not_goods(self):
        e = sale()
        self.assertEqual(e.get('item_kind'), 'food')
        self.assertNotIn('goods', e)
        self.assertEqual(summary([e])['cohorts'][0]['unknown_reason'], 'food_served_value_unknown')

    def test_gprefix_goods(self):
        self.assertEqual(sale('goods')['item_kind'], 'goods')

    def test_arbitrary_valid_sku_uses_catalog(self):
        for sku in ('arbitrary:SKU-1', 'f00000001', 'G12345678'):
            for kind in ('goods', 'food'):
                self.assertEqual(sale(kind, sku)['item_kind'], kind)

    def test_unknown_catalog_without_price_keeps_kind_evidence(self):
        for catalog, state in (({}, 'missing'), ({'G12345678': None}, 'missing'),
                               ({'G12345678': {}}, 'missing'),
                               ({'G12345678': {'kind': None}}, 'null'),
                               ({'G12345678': {'kind': 'unknown'}}, 'invalid'),
                               ({'G12345678': {'kind': True}}, 'invalid'),
                               ({'G12345678': {'kind': []}}, 'invalid')):
            with self.subTest(state=state, catalog=catalog):
                e = b.normalize([row(item='G12345678')], catalog, OWN, 200)[0][0]
                self.assertEqual(e['item_kind'], 'unknown')
                self.assertEqual(e['catalog_kind_state'], state)
                self.assertEqual(b.validate_event(e), e)

    def test_ledger_event_kind_is_not_item_kind(self):
        for data in ({'kind': 'settle'}, {}, {'kind': 'food'}, {'item_kind': 'food'}):
            fixture = row(item='sku')
            fixture['data'].update(data)
            e = b.normalize([fixture], {'sku': {'kind': 'food'}}, OWN, 200)[0][0]
            self.assertEqual(e['item_kind'], 'food')
        e = b.normalize([row(item='sku', item_kind='food')], {}, OWN, 200)[0][0]
        self.assertEqual(e['item_kind'], 'unknown')

    def test_invalid_and_conflicting_ledger_evidence_unknown(self):
        for data in ({'item_kind': 'goods'}, {'item_kind': None}, {'item_kind': []},
                     {'kind': 'goods', 'item_kind': 'food'}, {'kind': 'unrecognized'}):
            fixture = row(item='sku')
            fixture['data'].update(data)
            e = b.normalize([fixture], {'sku': {'kind': 'food'}}, OWN, 200)[0][0]
            self.assertEqual(e['item_kind'], 'unknown')
            self.assertEqual(e['item_kind_source'], 'catalog_ledger_conflict')
            self.assertEqual(b.validate_event(e), e)

    def test_v2_metadata_required_strict_and_roundtrip(self):
        e = sale()
        keys = ('item_kind', 'item_kind_source', 'catalog_kind', 'catalog_kind_state',
                'catalog_kind_observed_at', 'ledger_item_kind', 'ledger_kind_state')
        for key in keys:
            for value in (None, True, [], float('nan'), 'bogus'):
                with self.subTest(key=key, value=value):
                    self.assertIsNone(b.validate_event(dict(e, **{key: value})))
            bad = dict(e)
            del bad[key]
            self.assertIsNone(b.validate_event(bad))
        for changes in ({'item_kind': 'goods'}, {'catalog_kind': 'goods'},
                        {'catalog_kind_state': 'missing'}, {'catalog_kind_observed_at': 201},
                        {'ledger_kind_state': 'valid'}, {'goods': False}):
            self.assertIsNone(b.validate_event(dict(e, **changes)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            b.append_events([e], path)
            clean = list(b.read_events(path))
            self.assertEqual(clean, [e])
            self.assertEqual(summary(clean), summary([e]))

    def test_legacy_gfood_unknown_no_formula(self):
        old = legacy()
        before = copy.deepcopy(old)
        broken = b.normalize([row('broken', 2, ts=200, refunded=0)], {}, OWN, 300)[0][0]
        broken['version'] = 1
        result = summary([old, broken])
        c = result['cohorts'][0]
        self.assertEqual(c['item_kind_source'], 'legacy_kind_unknown')
        self.assertEqual(c['unknown_reason'], 'legacy_kind_unknown')
        self.assertIsNone(c['known_broken_contribution'])
        self.assertEqual(result['item_kind_orders'], dict(goods=0, food=0, unknown=1))
        self.assertEqual(old, before)

    def test_mixed_duplicate_both_orders_safe_provenance(self):
        old, new = legacy(), sale()
        for events in ([old, new], [new, old], [old, new, old, new]):
            r = summary(events)
            c = r['cohorts'][0]
            self.assertEqual(c['item_kind'], 'food')
            self.assertEqual(c['kind_versions_seen'], [1, 2])
            self.assertEqual(c['unknown_reason'], 'food_served_value_unknown')
            self.assertEqual(r['item_kind_settle_events'], dict(goods=0, food=1, unknown=0))

    def test_snapshot_kind_change_not_ledger_conflict(self):
        first, later = sale(), sale('goods')
        later.update(observed_at=300, catalog_kind_observed_at=300, list_price=200)
        c = summary([first, later])['cohorts'][0]
        self.assertEqual(c['item_kind'], 'food')
        self.assertEqual(c['surface_discount'], .15)
        self.assertTrue(c['observation_kind_changed'])
        self.assertEqual(c['unknown_reason'], 'food_served_value_unknown')

    def test_same_seq_original_facts_conflicts_detected(self):
        for key, value in (('paid_amount', 86), ('order', 'other'), ('sku', 'other'), ('unit', 'other')):
            e = sale('goods')
            c = summary([e, dict(e, **{key: value})])['cohorts'][0]
            self.assertEqual(c['unknown_reason'], 'conflicting_events')

    def test_stored_snapshot_frozen_not_catalog_reclassified(self):
        catalog = {'G12345678': {'list_price': 100, 'kind': 'food'}}
        events = b.prepare([row(item='G12345678')], catalog, OWN, 200)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            b.append_events(events, path)
            original = path.read_bytes()
            catalog['G12345678']['kind'] = 'goods'
            self.assertEqual(summary(b.read_events(path))['cohorts'][0]['item_kind'], 'food')
            self.assertEqual(path.read_bytes(), original)

    def test_food_under_twenty_is_not_goods_warning(self):
        e = sale()  # 15% food, policy is 10%; collector must not infer goods 20% violation.
        c = summary([e])['cohorts'][0]
        self.assertEqual(c['item_kind'], 'food')
        self.assertEqual(c['surface_discount'], .15)
        self.assertIsNone(e['warranty_hours'])
        self.assertEqual(c['unknown_reason'], 'food_served_value_unknown')
        self.assertIsNone(summary([e])['official_saved'])

    def test_mixed_following_events_counts_orders_vs_events(self):
        first = sale()
        second = dict(sale('goods'), seq=2, order='two')
        third = dict(legacy(), seq=3, order='three')
        fourth = dict(second, seq=4)
        r = summary([third, first, second, first, fourth])
        self.assertEqual(r['item_kind_orders'], dict(goods=1, food=1, unknown=1))
        self.assertEqual(r['item_kind_settle_events'], dict(goods=2, food=1, unknown=1))
        self.assertEqual(len(r['cohorts']), 3)

    def test_same_order_distinct_event_kinds_keep_event_denominator(self):
        r = summary([sale(), dict(sale('goods'), seq=2)])
        self.assertEqual(r['item_kind_orders'], dict(goods=0, food=1, unknown=0))
        self.assertEqual(r['item_kind_settle_events'], dict(goods=1, food=1, unknown=0))
        self.assertEqual(r['cohorts'][0]['unknown_reason'], 'conflicting_events')

    def test_mixed_file_corrupt_tail_preserved_then_valid_v2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            prefix = (json.dumps(legacy()) + '\n{bad').encode()
            path.write_bytes(prefix)
            b.append_events([sale(), dict(sale('goods'), seq=2, order='two')], path)
            self.assertTrue(path.read_bytes().startswith(prefix + b'\n'))
            r = summary(b.read_events(path))
            self.assertEqual(r['coverage']['corrupt_unknown'], 1)
            self.assertEqual(r['item_kind_orders'], dict(goods=1, food=1, unknown=0))

    def test_legacy_boolean_not_trusted_or_sku_inferred(self):
        for sku in ('G12345678', 'unrelated', 'f00000001'):
            for goods in (True, False):
                e = dict(legacy(), sku=sku, goods=goods)
                self.assertIsNotNone(b.validate_event(e))
                self.assertEqual(summary([e])['cohorts'][0]['item_kind'], 'unknown')
        self.assertIsNone(b.validate_event(dict(legacy(), goods=1)))

    def test_explicit_ledger_kind_contradiction_is_integrity_conflict(self):
        known_goods = dict(sale('goods'), ledger_item_kind='goods', ledger_kind_state='valid')
        known_food = dict(known_goods, ledger_item_kind='food', item_kind='unknown',
                          item_kind_source='catalog_ledger_conflict')
        broken = b.normalize([row('broken', 9, ts=200, refunded=0)], {}, OWN, 300)[0][0]
        for observations in permutations([known_goods, known_food, broken]):
            c = summary(observations)['cohorts'][0]
            self.assertEqual(c['unknown_reason'], 'conflicting_events')
            self.assertEqual(c['item_kind'], 'unknown')
            self.assertIsNone(c['known_broken_contribution'])

    def test_explicit_ledger_facts_survive_absent_snapshot(self):
        absent = sale('goods')
        goods = dict(absent, ledger_item_kind='goods', ledger_kind_state='valid')
        food = dict(absent, ledger_item_kind='food', ledger_kind_state='valid',
                    item_kind='unknown', item_kind_source='catalog_ledger_conflict')
        for observations in permutations([absent, goods, food]):
            c = summary(observations)['cohorts'][0]
            self.assertEqual(c['unknown_reason'], 'conflicting_events')
            self.assertEqual(c['item_kind'], 'unknown')

    def test_absent_and_matching_known_ledger_metadata_not_conflict(self):
        absent = sale('goods')
        known = dict(absent, ledger_item_kind='goods', ledger_kind_state='valid')
        broken = b.normalize([row('broken', 9, ts=200, refunded=0)], {}, OWN, 300)[0][0]
        for observations in ([absent, known, broken], [known, absent, broken]):
            c = summary(observations)['cohorts'][0]
            self.assertEqual(c['item_kind'], 'goods')
            self.assertEqual(c['known_broken_contribution'], -.85)

    def test_known_ledger_kind_against_frozen_catalog_not_lost(self):
        absent = sale('goods')
        known = dict(absent, ledger_item_kind='food', ledger_kind_state='valid',
                     item_kind='unknown', item_kind_source='catalog_ledger_conflict')
        for observations in ([absent, known], [known, absent]):
            r = summary(observations)
            c = r['cohorts'][0]
            self.assertEqual(c['item_kind'], 'unknown')
            self.assertEqual(c['unknown_reason'], 'item_kind_unknown')
            self.assertNotIn('duplicate_integrity_conflict', r['coverage'])

    def test_future_collision_order_marked_in_every_arrival_order(self):
        a = dict(sale('goods'), order='A')
        collision = dict(a, order='B')
        later_b = dict(collision, seq=2)
        broken_b = b.normalize([row('broken', 9, order='B', ts=200, refunded=0)], {}, OWN, 300)[0][0]
        # Exact reported sequence first, then every arrival permutation.
        for observations in permutations([a, collision, later_b, broken_b]):
            c = next(c for c in summary(observations)['cohorts'] if c['order'] == 'B')
            self.assertEqual(c['unknown_reason'], 'conflicting_events')
            self.assertIsNone(c['known_broken_contribution'])

    def test_both_collision_orders_persist_until_future_insert(self):
        a = dict(sale('goods'), order='A')
        collision = dict(a, order='B')
        later_a, later_b = dict(a, seq=3), dict(collision, seq=2)
        for observations in permutations([a, collision, later_a, later_b]):
            cohorts = {c['order']: c for c in summary(observations)['cohorts']}
            self.assertEqual(set(cohorts), {'A', 'B'})
            for c in cohorts.values():
                self.assertEqual(c['unknown_reason'], 'conflicting_events')
                self.assertIsNone(c['known_broken_contribution'])


if __name__ == '__main__':
    unittest.main()
