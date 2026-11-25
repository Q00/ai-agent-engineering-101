"""Deterministic discovery tests; no external market or model calls."""
from pathlib import Path
import tempfile
import unittest

from r3_common import atomic_json, read_json
from r3_discovery import analyze, build_candidates, validate_analysis
from r3_memory import Memory


NOW = 1793707200.
CATALOG = {
    'tea': {'name': 'Tea', 'category': 'drinks', 'kind': 'goods', 'list_price': 40},
    'tea2': {'name': 'Related tea', 'category': 'drinks', 'kind': 'goods', 'list_price': 44},
    'luxury': {'name': 'Collector tea', 'category': 'drinks', 'kind': 'goods', 'list_price': 400},
    'cup': {'name': 'Cup', 'category': 'kitchen', 'kind': 'goods', 'list_price': 40},
    'pen': {'name': 'Pen', 'category': 'office', 'kind': 'goods', 'list_price': 40},
}


def event(kind, key, data, age=0):
    return {'kind': kind, 'key': key, 'data': data, 'ts': NOW - age}


def choose_all(system, state, validator, purpose, deadline, **kwargs):
    return validator({'hypotheses': [{'sku': row['sku'], 'evidence_ids': row['evidence_ids'][:1],
                                     'reason': 'Bounded observation or exploration.'}
                                    for row in state['candidates'][:8]], 'summary': 'Limited evidence.'})


class DiscoveryTests(unittest.TestCase):
    def test_demand_metadata_and_forum_are_not_sales(self):
        events = [event('demand', 'drinks', {'category': 'drinks', 'skus': 50000,
                                            'median_price': 400}),
                  event('forum', 'claim', {'text': 'Tea sells a million units.'})]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['observations'], 0)
        self.assertEqual(result['metrics'], {})
        self.assertTrue(result['initial_exploration'])
        self.assertEqual(len({row['category'] for row in result['candidates']}),
                         len(result['candidates']))
        self.assertTrue(all(row['score'] == 0 and row['demand_status'] == 'initial_exploration'
                            for row in result['candidates']))

    def test_related_unseen_is_unknown_and_price_comparable(self):
        result = build_candidates(CATALOG, [event('market_settle', '7', {'seq': 7, 'item': 'tea'})], now=NOW)
        rows = {row['sku']: row for row in result['candidates']}
        self.assertEqual(rows['tea']['observed_sales'], 1)
        self.assertEqual(rows['tea2']['demand_status'], 'related_unseen')
        self.assertEqual(rows['tea2']['observed_sales'], 0)
        self.assertEqual(rows['tea2']['evidence_ids'], ['market_settle:7'])
        self.assertNotIn('luxury', rows)
        self.assertIsNone(result['metrics']['drinks']['growth'])

    def test_duplicate_order_is_one_category_observation(self):
        data = {'order': 'order-1', 'category': 'drinks', 'acceptable': ['tea', 'tea2', 'tea']}
        result = build_candidates(CATALOG, [event('order_seen', 'a', data),
                                            event('order_seen', 'b', data)], now=NOW)
        self.assertEqual(result['metrics']['drinks']['eligible_orders'], 1)
        self.assertEqual(result['metrics']['drinks']['sales'], 0)
        self.assertEqual(result['observations'], 1)
        self.assertTrue(all(row['eligible_source'] == 'own_buyer_biased' for row in result['candidates']))
        self.assertEqual(sum(row['score'] for row in result['candidates']), .25)

    def test_public_and_private_receipt_deduplicate(self):
        events = [event('market_settle', '7', {'seq': 7, 'item': 'tea', 'order': 'o1'}),
                  event('settlement', 'local', {'sku': 'tea', 'order': 'o1', 'settled': {'ledger': {'seq': 7}}}),
                  event('market_settle', 'copy', {'seq': 7, 'item': 'tea'})]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['metrics']['drinks']['sales'], 1)
        self.assertEqual(result['observations'], 1)

    def test_scalar_settlement_ledger_matches_public_sequence(self):
        events = [event('market_settle', '7', {'seq': 7, 'item': 'tea'}),
                  event('settlement', 'local', {'sku': 'tea', 'settled': {'ledger': 7}})]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['metrics']['drinks']['sales'], 1)

    def test_model_evidence_is_bounded_without_losing_numerical_counts(self):
        events = [event('market_settle', str(i), {'seq': i, 'item': 'tea'}) for i in range(100)]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['metrics']['drinks']['sales'], 100)
        self.assertEqual(result['observations'], 100)
        self.assertLessEqual(len(result['evidence']), 16)
        self.assertTrue(all(len(row['evidence_ids']) <= 8 for row in result['candidates']))

    def test_time_decay_and_growth_need_evidence_in_both_windows(self):
        events = [event('market_settle', 'old', {'seq': 1, 'item': 'tea'}, age=21600)]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['metrics']['drinks']['sales_weight'], .5)
        events = [event('market_settle', str(i), {'seq': i, 'item': 'tea'},
                        age=100 if i < 6 else 22000) for i in range(9)]
        result = build_candidates(CATALOG, events, now=NOW)
        self.assertEqual(result['metrics']['drinks']['growth'], 1.)

    def test_fabricated_evidence_and_unknown_sku_rejected(self):
        inputs = build_candidates(CATALOG, [event('market_settle', '7', {'seq': 7, 'item': 'tea'})], now=NOW)
        for row in ({'sku': 'tea', 'evidence_ids': ['made-up'], 'reason': 'x'},
                    {'sku': 'missing', 'evidence_ids': [], 'reason': 'x'},
                    {'sku': 'tea', 'evidence_ids': [], 'reason': 'x'}):
            with self.assertRaises(ValueError):
                validate_analysis({'hypotheses': [row], 'summary': ''}, inputs)

    def test_analysis_ttl_tracks_input_version_and_refreshes_after_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory)
            atomic_json(md / 'catalog.json', CATALOG)
            memory = Memory(md)
            calls = []
            def fake(*args, **kwargs):
                calls.append(args[3])
                return choose_all(*args, **kwargs)
            first = analyze(md, now=NOW, decide_fn=fake, memory=memory)
            memory.record('market_settle', '1', {'seq': 1, 'item': 'tea'}, ts=NOW + 1)
            second = analyze(md, now=NOW + 2, decide_fn=fake, memory=memory)
            self.assertEqual(len(calls), 1)
            self.assertEqual(first['input_version'], second['input_version'])
            self.assertEqual(second['current_inputs']['observations'], 1)
            third = analyze(md, now=NOW + 901, decide_fn=fake, memory=memory)
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(first['input_version'], third['input_version'])
            self.assertEqual(read_json(md / 'analysis.json')['generated_at'], NOW + 901)

    def test_unavailable_model_records_failure_without_hypotheses(self):
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory)
            atomic_json(md / 'policy.json', {'fallback_enabled': False})
            atomic_json(md / 'catalog.json', CATALOG)
            memory = Memory(md)
            def fail(*args, **kwargs):
                raise RuntimeError('offline model unavailable')
            result = analyze(md, now=NOW, decide_fn=fail, memory=memory)
            self.assertEqual(result['status'], 'unavailable')
            self.assertEqual(result['hypotheses'], [])
            self.assertEqual(len(memory.events(kind='discovery_error')), 1)


if __name__ == '__main__':
    unittest.main()
