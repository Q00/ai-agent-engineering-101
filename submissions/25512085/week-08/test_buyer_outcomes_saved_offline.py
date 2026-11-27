"""Step 1: Saved is not normalized surplus; synthetic offline replay only."""
import copy
import unittest

import buyer_outcomes as outcomes
from test_buyer_outcomes_offline import CAT, OWN, replay, row


class SavedContributionTests(unittest.TestCase):
    def cohort(self, refund, **kwargs):
        return replay([row(), row('broken', 2, ts=200, refunded=refund)], **kwargs)['cohorts'][0]

    def assert_unknown(self, c, reason):
        self.assertIsNone(c['saved_contribution'])
        self.assertIsNone(c['normalized_surplus_contribution'])
        self.assertEqual(c['unknown_reason'], reason)
        self.assertEqual(c['normalized_surplus_unknown_reason'], reason)

    def test_unrefunded_full_unserved_saved_is_minus_one_not_price_ratio(self):
        c = self.cohort(0)
        self.assertEqual(c['saved_contribution'], -1)
        self.assertEqual(c['normalized_surplus_contribution'], -.6)
        # Compatibility: the deprecated field still means legacy surplus, NOT Saved.
        self.assertEqual(c['known_broken_contribution'], -.6)
        self.assertIsNone(c['unknown_reason'])
        self.assertIsNone(c['normalized_surplus_unknown_reason'])

    def test_full_refund_both_zero(self):
        c = self.cohort(60)
        self.assertEqual(c['saved_contribution'], 0)
        self.assertEqual(c['normalized_surplus_contribution'], 0)
        self.assertEqual(c['known_broken_contribution'], 0)

    def test_partial_refund_saved_unknown_but_surplus_known(self):
        c = self.cohort(30)
        self.assertIsNone(c['saved_contribution'])
        self.assertEqual(c['unknown_reason'], 'partial_refund_semantics_unknown')
        self.assertEqual(c['normalized_surplus_contribution'], -.3)
        self.assertIsNone(c['normalized_surplus_unknown_reason'])
        self.assertIsNone(c['known_broken_contribution'])  # Preserve legacy coverage too.

    def test_overrefund_invalid_for_both(self):
        self.assert_unknown(self.cohort(61), 'refund_unknown')

    def test_missing_refund_not_zero(self):
        self.assert_unknown(self.cohort(None), 'refund_unknown')

    def test_zero_paid_zero_refund_uses_unrefunded_unserved_rule(self):
        c = replay([row(amount=0), row('broken', 2, ts=200, refunded=0)])['cohorts'][0]
        self.assertEqual(c['saved_contribution'], -1)
        self.assertIsNone(c['unknown_reason'])
        self.assertEqual(c['normalized_surplus_contribution'], 0)

    def test_elapsed_six_hours_never_proves_success(self):
        c = replay([row()], now=10**8)['cohorts'][0]
        self.assertTrue(c['mature_6h'])
        self.assertFalse(c['observation_complete'])
        self.assert_unknown(c, 'ledger_coverage_unproven')

    def test_food_without_served_evidence_stays_unknown(self):
        catalog = {'g00000001': {'list_price': 100, 'kind': 'food'}}
        self.assert_unknown(self.cohort(0, catalog=catalog), 'food_served_value_unknown')

    def test_missing_or_conflicting_kind_stays_unknown(self):
        for kind in (None, 'food'):
            catalog = {'g00000001': {'list_price': 100}}
            if kind:
                catalog['g00000001']['kind'] = kind
            rows = [row(item_kind='goods'), row('broken', 2, ts=200, refunded=0)]
            self.assert_unknown(replay(rows, catalog=catalog)['cohorts'][0], 'item_kind_unknown')

    def test_legacy_kind_not_reconstructed(self):
        events, _ = outcomes.normalize([row(), row('broken', 2, ts=200, refunded=0)], CAT, OWN, 200)
        events[0] = {k: events[0][k] for k in ('kind', 'buyer', 'order', 'seq', 'ts', 'observed_at',
                     'unit', 'sku', 'paid_amount', 'list_price', 'list_price_source', 'grade', 'warranty_hours')}
        events[0].update(version=1, goods=True)
        self.assert_unknown(outcomes.summarize(events, OWN, 30000)['cohorts'][0], 'legacy_kind_unknown')

    def test_exact_order_and_unit_join_required(self):
        c = replay([row(), row('broken', 2, order='another', ts=200, refunded=0)])['cohorts'][0]
        self.assert_unknown(c, 'ledger_coverage_unproven')
        c = replay([row(unit='u1'), row('broken', 2, unit='u2', ts=200, refunded=0)])['cohorts'][0]
        self.assert_unknown(c, 'unit_mismatch')

    def test_ledger_conflicts_remain_unknown(self):
        c = replay([row(), row(amount=61), row('broken', 2, ts=200, refunded=0)])['cohorts'][0]
        self.assert_unknown(c, 'conflicting_events')

    def test_truncated_replay_never_keeps_known_contributions(self):
        rows = [row(), row('broken', 2, ts=200, refunded=0), row(seq=3, order='second')]
        c = replay(rows, max_orders=1)['cohorts'][0]
        self.assert_unknown(c, 'replay_truncated_unknown')

    def test_counts_share_retained_order_denominator_not_official_deals(self):
        rows = []
        for i, refund in enumerate((0, 60, 30, None)):
            rows.extend([row(seq=i*2+1, order='o'+str(i)),
                         row('broken', i*2+2, order='o'+str(i), ts=200, refunded=refund)])
        events = outcomes.prepare(rows + [row(order=None)], CAT, OWN, 200)
        original = copy.deepcopy(events)
        result = outcomes.summarize(events + events, OWN, 30000)
        self.assertEqual(events, original)  # No journal/schema migration or mutation.
        self.assertEqual(result['contribution_counts'], dict(
            retained_order_denominator=4, saved_known_orders=2, saved_unknown_orders=2,
            normalized_surplus_known_orders=3, normalized_surplus_unknown_orders=1))
        self.assertEqual(result['coverage']['no_order'], 2)
        self.assertIsNone(result['official_saved'])
        self.assertFalse(result['complete'])
        self.assertTrue(all(not c['observation_complete'] for c in result['cohorts']))


if __name__ == '__main__':
    unittest.main()
