"""Synthetic evidence only; no production runtime or credentials."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import buyer_outcomes as outcomes
import store

OWN = 'mjforge'
CAT = {'g00000001': {'list_price': 100, 'kind': 'goods', 'category': 'goods'}}


def row(kind='settle', seq=1, order='order-1', buyer=OWN, ts=100, **extra):
    data = dict(order=order, buyer=buyer, item='g00000001', amount=60,
                grade='A', warranty_hours=72)
    data.update(extra)
    return dict(kind=kind, seq=seq, ts=ts, round=3, data=data)


def replay(rows, now=30000, catalog=None, **kw):
    events, _ = outcomes.normalize(rows, CAT if catalog is None else catalog, OWN, 200)
    return outcomes.summarize(events, OWN, now, **kw)


class OutcomesTests(unittest.TestCase):
    def test_selected_is_not_paid(self):
        self.assertEqual(replay([row('result')])['cohorts'], [])
        self.assertEqual(replay([row('receipt_selected')])['cohorts'], [])

    def test_actual_settled_amount_not_receipt_price(self):
        event = outcomes.normalize([row(amount=61, price=2, receipt={'price': 1})], CAT, OWN, 200)[0][0]
        self.assertEqual(event['paid_amount'], 61)
        self.assertNotIn('receipt', event)

    def test_cross_buyer_and_seller_refund_excluded(self):
        result = replay([row(), row('broken', 2, buyer='someone', refunded=60, shop=OWN)])
        self.assertIsNone(result['cohorts'][0]['known_broken_contribution'])

    def test_out_of_order_exact_order_full_refund(self):
        result = replay([row('broken', 2, ts=200, refunded=60), row()])
        cohort = result['cohorts'][0]
        self.assertEqual(cohort['known_broken_contribution'], 0)
        self.assertEqual(cohort['broken_ts'], 200)

    def test_duplicates_restart_idempotent(self):
        rows = [row(), row('broken', 2, ts=200, refunded=0)]
        self.assertEqual(replay(rows), replay(rows + rows))

    def test_no_warranty_break_loss(self):
        result = replay([row(warranty_hours=0), row('broken', 2, ts=200, refunded=0)])
        self.assertEqual(result['cohorts'][0]['known_broken_contribution'], -.6)

    def test_partial_refund_unknown(self):
        c = replay([row(), row('broken', 2, ts=200, refunded=30)])['cohorts'][0]
        self.assertIsNone(c['known_broken_contribution'])
        self.assertEqual(c['unknown_reason'], 'partial_refund_semantics_unknown')

    def test_missing_refund_not_zero(self):
        c = replay([row(), row('broken', 2, ts=200)])['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'refund_unknown')

    def test_name_collision_never_joined(self):
        events, counts = outcomes.normalize([row(), row('broken', 2, order=None, name='same')], CAT, OWN, 200)
        self.assertEqual(counts, {'no_order': 1})
        self.assertIsNone(outcomes.summarize(events, OWN, 30000)['cohorts'][0]['broken_ts'])

    def test_missing_identity_counter(self):
        self.assertEqual(outcomes.normalize([row(buyer=None)], CAT, OWN, 200), ([], {'no_identity': 1}))

    def test_price_snapshot_immutable_first_seq(self):
        events, _ = outcomes.normalize([row()], CAT, OWN, 200)
        changed, _ = outcomes.normalize([row()], {'g00000001': {'list_price': 200}}, OWN, 300)
        c = outcomes.summarize(events + changed, OWN, 30000)['cohorts'][0]
        self.assertEqual(c['surface_discount'], .4)
        self.assertEqual(events[0]['list_price_source'], 'catalog_at_observation')

    def test_missing_snapshot_unknown_not_budget(self):
        c = replay([row(budget=100)], catalog={})['cohorts'][0]
        self.assertIsNone(c['surface_discount'])
        self.assertEqual(c['unknown_reason'], 'item_kind_unknown')

    def test_mature_is_not_complete_even_a72(self):
        c = replay([row(warranty_hours=72)])['cohorts'][0]
        self.assertTrue(c['mature_6h'])
        self.assertFalse(c['observation_complete'])
        self.assertEqual(c['unknown_reason'], 'ledger_coverage_unproven')
        self.assertFalse(replay([row()], now=200)['cohorts'][0]['mature_6h'])

    def test_late_break_retained(self):
        c = replay([row(), row('broken', 2, ts=21500, refunded=0)])['cohorts'][0]
        self.assertEqual(c['known_broken_contribution'], -.6)

    def test_unit_mismatch_unknown(self):
        c = replay([row(unit='unit-1'), row('broken', 2, ts=200, unit='unit-2', refunded=60)])['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'unit_mismatch')

    def test_break_before_settle_unknown(self):
        c = replay([row(), row('broken', 2, ts=99, refunded=60)])['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'break_before_settlement')

    def test_food_no_goods_formula(self):
        c = replay([row(item='f00000001')], catalog={'f00000001': {'list_price': 100, 'kind': 'food'}})['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'food_served_value_unknown')

    def test_conflicting_refunds_not_summed(self):
        c = replay([row(), row('broken', 2, ts=200, refunded=30),
                    row('broken', 3, ts=201, refunded=30)])['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'conflicting_events')

    def test_orphan_break_count(self):
        self.assertEqual(replay([row('broken', 2)])['coverage']['broken_without_settle'], 1)

    def test_bounded_replay_unknown_eviction(self):
        result = replay([row(order='a'), row(seq=2, order='b')], max_orders=1)
        self.assertEqual(len(result['cohorts']), 1)
        self.assertEqual(result['coverage']['pending_eviction_unknown'], 1)
        with patch.object(outcomes, 'MAX_EVENTS', 1):
            self.assertEqual(replay([row(), row(seq=2)])['coverage']['event_cap_unknown'], 1)

    def test_no_secrets_in_allowlist(self):
        rows = [row(JWT='SECRET', sig_buyer='SECRET', budget=999, receipt='SECRET', name='SECRET')]
        self.assertNotIn('SECRET', json.dumps(outcomes.normalize(rows, CAT, OWN, 200)))

    def test_append_restart_corrupt_tail_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            outcomes.observe([row()], CAT, OWN, 200, path)
            before = path.read_bytes()
            with path.open('ab') as stream:
                stream.write(b'{bad')
            outcomes.observe([row('broken', 2, ts=200, refunded=60)], CAT, OWN, 300, path)
            self.assertTrue(path.read_bytes().startswith(before + b'{bad\n'))
            result = outcomes.summarize(outcomes.read_events(path), OWN, 30000)
            self.assertEqual(result['coverage']['corrupt_unknown'], 1)
            self.assertEqual(result['cohorts'][0]['known_broken_contribution'], 0)

    def test_io_failure_safe_diagnostic(self):
        with patch.object(Path, 'mkdir', side_effect=OSError('SECRET')), patch.object(outcomes, 'diagnostic') as diag:
            outcomes.observe([row()], CAT, OWN, 200, Path('unused/journal.jsonl'))
        diag.assert_called_once_with('journal_write_failed_unknown')

    def test_capacity_no_rotation_or_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            outcomes.observe([row()], CAT, OWN, 200, path)
            original = path.read_bytes()
            with patch.object(outcomes, 'MAX_BYTES', len(original)), patch.object(outcomes, 'diagnostic') as diag:
                outcomes.observe([row(seq=2)], CAT, OWN, 300, path)
            self.assertEqual(path.read_bytes(), original)
            diag.assert_called_once_with('journal_capacity_unknown')

    def test_ledger_ingestion_integration_no_policy_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = store.default_hub()
            with patch.object(store, 'BASE_DIR', Path(tmp)), patch.object(store, 'OWN_NAME', OWN):
                with store.mutate_hub(Path(tmp) / 'hub.json') as hub:
                    store.apply_ledger_rows(hub, [row()], CAT)
                    store.apply_ledger_rows(hub, [row('broken', 2, ts=200, refunded=60)], CAT)
            path = Path(tmp) / 'runtime/buyer-outcomes-v1.jsonl'
            result = outcomes.summarize(outcomes.read_events(path), OWN, 30000)
            self.assertEqual(result['cohorts'][0]['known_broken_contribution'], 0)
            before = copy.deepcopy(result)
            store.compact_hub(hub, 30000)
            self.assertEqual(outcomes.summarize(outcomes.read_events(path), OWN, 30000), before)
            self.assertNotIn('buyer_outcomes', hub)
            self.assertEqual(hub['my_purchases'], [])

    def test_ingestion_logging_failure_does_not_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'hub.json'
            with patch.object(store, 'BASE_DIR', Path(tmp)), patch.object(Path, 'mkdir', side_effect=OSError()), patch.object(outcomes, 'diagnostic') as diag:
                with store.mutate_hub(path) as hub:
                    store.apply_ledger_rows(hub, [row()], CAT)
            diag.assert_called_once_with('journal_write_failed_unknown')
            self.assertEqual(store.load_hub(path)['ledger_seq'], 1)

    def test_batch_memory_cap_explicit(self):
        with patch.object(outcomes, 'MAX_BATCH_ROWS', 1):
            events, counts = outcomes.normalize([row(), row(seq=2)], CAT, OWN, 200)
        self.assertEqual(len(events), 1)
        self.assertEqual(counts, {'batch_cap_unknown': 1})

    def test_thread_writers_preserve_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            def write(index):
                outcomes.observe([row(seq=index, order='o-' + str(index))], CAT, OWN, 200, path)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(write, range(1, 17)))
            result = outcomes.summarize(outcomes.read_events(path), OWN, 30000)
            self.assertEqual(len(result['cohorts']), 16)
            self.assertNotIn('corrupt_unknown', result['coverage'])

    def test_failed_lock_never_writes_unlocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            with patch.object(outcomes, 'exclusive', side_effect=BlockingIOError()), patch.object(outcomes, 'diagnostic') as diag:
                outcomes.observe([row()], CAT, OWN, 200, path)
            self.assertFalse(path.exists())
            diag.assert_called_once_with('journal_write_failed_unknown')

    def test_multi_day_retention_no_ttl(self):
        result = replay([row(), row('broken', 2, ts=200, refunded=0)], now=10 * 86400)
        self.assertEqual(result['cohorts'][0]['known_broken_contribution'], -.6)

    def test_refund_over_paid_unknown(self):
        c = replay([row(), row('broken', 2, ts=200, refunded=61)])['cohorts'][0]
        self.assertEqual(c['unknown_reason'], 'refund_unknown')

    def test_oversized_corrupt_line_bounded_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.jsonl'
            path.write_bytes(b'x' * 10000 + b'\n')
            outcomes.observe([row()], CAT, OWN, 200, path)
            result = outcomes.summarize(outcomes.read_events(path), OWN, 30000)
            self.assertEqual(result['coverage']['corrupt_unknown'], 1)
            self.assertEqual(len(result['cohorts']), 1)


if __name__ == '__main__':
    unittest.main()
