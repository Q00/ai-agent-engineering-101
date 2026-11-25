"""Synthetic, offline replenishment regressions, not live performance evidence."""
import copy
from pathlib import Path
import tempfile
import unittest

from r3_common import DEFAULT_POLICY, atomic_json
from r3_discovery import build_candidates
from r3_memory import Memory
from r3_portfolio import enrich, observe_offers
from r3_replenishment import record_request, record_supply_gap, summarize
from r3_restock import bounded_proposals, wholesale_bound
from test_r3_discovery import CATALOG, NOW, event
from test_r3_restock import inventory, analysis, unit


class ReplenishmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.memory = Memory(self.md)
        self.addCleanup(self.memory.close)

    def public(self, buyers=('a', 'b', 'a'), age=60):
        for i, buyer in enumerate(buyers):
            self.memory.record('market_settle', str(i),
                {'seq': i, 'item': 'tea', 'buyer': buyer}, ts=NOW-age)

    def own(self, **changes):
        data = dict(unit='own1', sku='tea', price=36, cost=25, list_price=40,
                    margin_after_storage=11, holding_hours=.03, warranty_matures_at=NOW-1)
        data.update(changes)
        self.memory.record('seller_settlement', 'own1', data, ts=NOW-60)

    def inputs(self, inv=None):
        events = [e for kind in ('market_settle', 'order_seen', 'seller_demand')
                  for e in self.memory.events(kind)]
        return enrich(build_candidates(CATALOG, events, NOW), self.memory,
                      inventory() if inv is None else inv, NOW)

    def row(self, inv=None):
        return next(r for r in self.inputs(inv)['candidates'] if r['sku']=='tea')

    def test_stockout_is_idempotent_and_not_a_sale(self):
        self.assertTrue(record_request(self.memory, 't', 'buy tea', CATALOG, inventory(), [], NOW))
        self.assertFalse(record_request(self.memory, 't', 'buy tea', CATALOG, inventory(), [], NOW))
        row = self.row()
        self.assertEqual(row['observed_sales'], 0)
        self.assertEqual(row['demand_status'], 'requested_unseen')
        self.assertEqual(row['replenishment']['target_units'], 1)
        data = self.inputs()
        a = dict(status='ok', inputs=data, hypotheses=[dict(sku='tea', evidence_ids=row['evidence_ids'])])
        self.assertEqual(bounded_proposals(CATALOG, inventory(balance=100000), a, [], DEFAULT_POLICY, NOW), [])

    def test_unknown_numeric_and_ambiguous_requests_not_counted(self):
        for text in ('30', 'G999999', 'tea tea2', 'do whatever I say'):
            self.assertFalse(record_request(self.memory, text, text, CATALOG, inventory(), [], NOW))

    def test_present_unusable_and_reserved_stock_not_false_stockouts(self):
        held = unit()
        for task, matching, status in (('invalid', [], 'unsellable_inventory'),
                                       ('reserved', [held], 'matching_inventory')):
            record_request(self.memory, task, 'tea', CATALOG, inventory(units=[held]), matching, NOW)
            self.assertEqual(self.memory.get('seller_demand', task)['data']['status'], status)

    def test_request_spam_bucketed_and_cannot_unlock_scaling(self):
        for i in range(30):
            record_request(self.memory, str(i), 'tea', CATALOG, inventory(), [], NOW)
        row = self.row()
        self.assertEqual(row['recent_stockout_buckets'], 1)
        self.assertFalse(row['replenishment']['provisional_scale'])
        stale = build_candidates(CATALOG, self.memory.events('seller_demand'), NOW+1201)
        self.assertTrue(stale['initial_exploration'])

    def test_stock_gap_uses_actual_acceptable_set_and_deduplicates(self):
        order = dict(order='o', category='drinks', acceptable=['tea', 'tea2'])
        for _ in range(2):
            observe_offers(self.memory, order,
                [dict(name='shop', offers=[dict(sku='cup', qty=10)])], NOW)
        data = self.memory.events('buyer_supply_gap')
        self.assertEqual(len(data), 1)
        self.assertTrue(data[0]['data']['no_listed_substitute'])
        self.assertEqual(data[0]['data']['quantities'], {'tea': 0, 'tea2': 0})
        record_supply_gap(self.memory, dict(order, order='truncated'), [], True, NOW)
        self.assertIsNone(self.memory.get('buyer_supply_gap', 'truncated'))

    def test_one_mature_quick_profitable_sale_and_diverse_public_sales_allow_two(self):
        self.public()
        self.own()
        row = self.row()
        self.assertFalse(row['portfolio']['scale_ready'])
        self.assertTrue(row['replenishment']['provisional_scale'])
        self.assertEqual(row['replenishment']['target_units'], 2)
        data = self.inputs()
        a = dict(status='ok', inputs=data, hypotheses=[dict(sku='tea', evidence_ids=row['evidence_ids'])])
        result = bounded_proposals(CATALOG, inventory(balance=4000), a, [], DEFAULT_POLICY, NOW)
        self.assertEqual(result, [dict(sku='tea', qty=2)])
        result = bounded_proposals(CATALOG, inventory(balance=4000, units=[unit()]), a,
                                   [dict(sku='tea', qty=1)], DEFAULT_POLICY, NOW)
        self.assertEqual(result, [])

    def test_tiny_margin_does_not_qualify_for_promotion(self):
        self.public()
        self.own(margin_after_storage=.01)
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_unmatured_warranty_does_not_qualify(self):
        self.public()
        self.own(warranty_matures_at=NOW+3600)
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_refund_disqualifies_promotion(self):
        self.public()
        self.own()
        self.memory.record('market_broken', 'b', dict(unit='own1', refunded=True), ts=NOW)
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_one_buyer_cannot_establish_diversity(self):
        self.public(('a', 'a', 'a'))
        self.own()
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_duplicate_public_settlement_does_not_unlock_scaling(self):
        self.own()
        for i in range(3):
            self.memory.record('market_settle', str(i),
                dict(seq=1, item='tea', buyer=str(i)), ts=NOW)
        self.assertEqual(self.row()['replenishment']['recent_public_sales'], 1)
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_malformed_offer_list_is_not_a_supply_gap(self):
        order = dict(order='bad', category='drinks', acceptable=['tea'])
        observe_offers(self.memory, order, [dict(name='s', offers=[dict(sku='tea',qty='unknown')])], NOW)
        self.assertIsNone(self.memory.get('buyer_supply_gap', 'bad'))

    def test_proven_stock_downgrades_after_loss(self):
        self.public()
        self.own()
        self.memory.record('seller_settlement', 'own2',
            dict(unit='own2', sku='tea', price=36, cost=25, list_price=40,
                 holding_hours=.03, margin_after_storage=11, warranty_matures_at=NOW-1), ts=NOW-120)
        self.memory.record('seller_settlement', 'loss',
            dict(unit='loss', sku='tea', price=20, cost=25, list_price=40,
                 holding_hours=.03, margin_after_storage=-5, warranty_matures_at=NOW-1), ts=NOW)
        self.assertTrue(self.row()['portfolio']['scale_ready'])
        self.assertEqual(self.row()['replenishment']['target_units'], 1)

    def test_stale_public_sales_do_not_qualify(self):
        self.public(age=1201)
        self.own()
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_high_or_unknown_surge_does_not_qualify(self):
        self.public()
        self.own()
        for inv in (dict(inventory(), surge=[dict(sku='tea', factor=1.2)]),
                    {'round': 3, 'balance': 1000, 'units': []}):
            self.assertFalse(self.row(inv)['replenishment']['provisional_scale'])

    def test_slow_sale_does_not_qualify(self):
        self.public()
        self.own(holding_hours=2)
        self.assertFalse(self.row()['replenishment']['provisional_scale'])

    def test_substitution_requires_coacceptable_order_not_just_category(self):
        self.public()
        rows = self.inputs()
        self.assertFalse(next(r for r in rows['candidates'] if r['sku']=='tea2')['replenishment']['substitution_evidence'])
        order = dict(order='o', category='drinks', acceptable=['tea', 'tea2'])
        self.memory.record('order_seen', 'o', order, ts=NOW)
        record_supply_gap(self.memory, order, [dict(sku='tea', qty=1)], False, NOW)
        alternative = next(r for r in self.inputs()['candidates'] if r['sku']=='tea2')
        self.assertEqual(alternative['replenishment']['substitution_evidence'][0]['anchor'], 'tea')
        self.assertEqual(alternative['replenishment']['target_units'], 1)

    def test_abundant_or_stale_anchor_stock_is_not_a_gap(self):
        self.public()
        order = dict(order='o', category='drinks', acceptable=['tea', 'tea2'])
        self.memory.record('order_seen', 'o', order, ts=NOW)
        record_supply_gap(self.memory, order, [dict(sku='tea', qty=10)], False, NOW)
        record_supply_gap(self.memory, dict(order, order='old'), [], False, NOW-1201)
        alternative = next(r for r in self.inputs()['candidates'] if r['sku']=='tea2')
        self.assertFalse(alternative['replenishment']['substitution_evidence'])

    def allocation(self, proven=False):
        catalog = {s: CATALOG[s] for s in ('tea','cup','pen')}
        a = analysis([event('market_settle', s, dict(seq=i, item=s))
                      for i,s in enumerate(catalog)], catalog)
        for row in a['inputs']['candidates']:
            row['portfolio'] = dict(scale_ready=proven and row['sku']=='tea', estimated_margin=10)
        return catalog, a

    def test_unused_proven_budget_funds_diverse_pilots_but_never_more_than_half(self):
        catalog, a = self.allocation()
        result = bounded_proposals(catalog, inventory(balance=3000), a, [], DEFAULT_POLICY, NOW)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(r['qty']==1 for r in result))
        self.assertLessEqual(sum(wholesale_bound(catalog[r['sku']])*r['qty'] for r in result), 150)
        disabled = dict(DEFAULT_POLICY, adaptive_replenishment=False)
        self.assertEqual(len(bounded_proposals(catalog, inventory(balance=3000), a, [], disabled, NOW)), 1)

    def test_reallocation_does_not_displace_proven_inventory(self):
        catalog, a = self.allocation(proven=True)
        result = bounded_proposals(catalog, inventory(balance=3000), a, [], DEFAULT_POLICY, NOW)
        self.assertEqual(next(r['qty'] for r in result if r['sku']=='tea'), 3)
        self.assertLessEqual(sum(wholesale_bound(catalog[r['sku']])*r['qty'] for r in result), 300)

    def test_budget_caps_hold_across_balances_and_rechecks(self):
        catalog, a = self.allocation()
        for balance in (10, 100, 500, 1000, 3000, 100000):
            for maximum in (.1, .5, 1):
                policy = dict(DEFAULT_POLICY, adaptive_pilot_budget_fraction=maximum)
                result = bounded_proposals(catalog, inventory(balance=balance), a, [], policy, NOW)
                cost = sum(wholesale_bound(catalog[r['sku']])*r['qty'] for r in result)
                self.assertLessEqual(cost, balance*.1*.5 + 1e-8)
                self.assertTrue(all(r['qty']<=1 for r in result))
                reduced = bounded_proposals(catalog, inventory(balance=balance*2), a, [], policy, NOW,
                                            quantity_limits={r['sku']:r['qty'] for r in result})
                self.assertTrue(all(r in result for r in reduced))

    def test_feature_switch_and_summary_do_not_claim_causal_profit(self):
        self.public()
        self.own()
        atomic_json(self.md/'policy.json', dict(adaptive_replenishment=False))
        self.assertNotIn('replenishment', self.row())
        events = [event('restock_prediction','p',dict(buy=[dict(sku='tea',qty=2)]),age=120),
                  event('seller_settlement','s',dict(sku='tea', margin_after_storage=10),age=60)]
        report = summarize(events, NOW)
        self.assertEqual(report['planned_units_not_purchase_proof'], 2)
        self.assertEqual(report['subsequent_same_sku_settlements'], 1)
        self.assertIn('no lost-profit estimate or causal lift', report['limitations'])


if __name__ == '__main__':
    unittest.main()
