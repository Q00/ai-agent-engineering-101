"""Supply windows and deterministic fallback, with no network/model execution."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from r3_common import atomic_json, read_json
from r3_memory import Memory
from r3_restock import plan_restock, supply_window
from r3_runtime import choose
from test_r3_discovery import CATALOG, NOW
from test_r3_restock import inventory, choose as fixture_choose


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)

    def test_circuit_skips_calls_and_logs_policy_not_model_results(self):
        decision = Mock(side_effect=RuntimeError('offline quota fixture'))
        def validate(value):
            if value != {'price': 10}:
                raise ValueError('invalid price')
            return value
        for _ in range(3):
            result = choose(self.md, decision, '', {}, validate, 'buyer',
                            time.monotonic() + 30, lambda: {'price': 10})
            self.assertEqual(result, {'price': 10})
        self.assertEqual(decision.call_count, 1)
        with Memory(self.md) as memory:
            self.assertEqual(len(memory.events('policy_fallback')), 3)
            self.assertEqual(memory.events('settlement'), [])

    def test_invalid_fallback_cannot_bypass_validator(self):
        def reject(value):
            raise ValueError('budget violation')
        with self.assertRaises(ValueError):
            choose(self.md, Mock(side_effect=TimeoutError()), '', {}, reject, 'buyer',
                   time.monotonic() + 10, lambda: {'price': 999999})

    def test_model_deadline_is_bounded_and_expired_order_does_not_fallback(self):
        def decision(system, state, validator, purpose, deadline, context):
            self.assertLessEqual(deadline, time.monotonic() + 20)
            return validator({'ok': True})
        choose(self.md, decision, '', {}, lambda x: x, 'buyer', time.monotonic() + 100, lambda: {})
        with self.assertRaises(TimeoutError):
            choose(self.md, decision, '', {}, lambda x: x, 'buyer', time.monotonic() - 1, lambda: {})

    def write_inventory(self, state, now=NOW):
        import os
        atomic_json(self.md / 'inventory.json', state)
        os.utime(self.md / 'inventory.json', (now, now))

    def test_missing_closed_future_and_nearly_closed_windows_block_model(self):
        atomic_json(self.md / 'catalog.json', CATALOG)
        for window in (None, {'open': False, 'opens_at': NOW + 100, 'closes_at': NOW + 280},
                       {'open': True, 'opens_at': NOW + 10, 'closes_at': NOW + 180},
                       {'open': True, 'opens_at': NOW - 100, 'closes_at': NOW + 10}):
            state = dict(inventory(), restock=window)
            self.write_inventory(state)
            model = Mock(side_effect=AssertionError('model must not run'))
            self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=model), {'buy': []})
            model.assert_not_called()
            self.assertFalse((self.md / 'restock_commitments.json').exists())

    def test_unfilled_intent_does_not_block_next_supply_window(self):
        atomic_json(self.md / 'catalog.json', CATALOG)
        for offset in (0, 1200):
            state = dict(inventory(), restock={'open': True, 'opens_at': NOW + offset - 5,
                                              'closes_at': NOW + offset + 175})
            self.write_inventory(state, NOW + offset)
            self.assertTrue(plan_restock(self.md, now=NOW + offset, decide_fn=fixture_choose)['buy'])
        self.assertLess(read_json(self.md / 'restock_commitments.json')['intents'][0]['expires_at'], NOW + 1600)

    def test_model_failure_still_emits_small_bounded_intent(self):
        atomic_json(self.md / 'catalog.json', CATALOG)
        self.write_inventory(inventory())
        model = Mock(side_effect=RuntimeError('quota fixture'))
        result = plan_restock(self.md, now=NOW, decide_fn=model)
        self.assertGreater(len(result['buy']), 0)
        self.assertLessEqual(sum(row['qty'] for row in result['buy']), 3)
        self.assertLessEqual(sum(CATALOG[row['sku']]['list_price'] * .75 * row['qty']
                                 for row in result['buy']), 100)
        self.assertEqual(model.call_count, 1)

    def test_window_closes_during_model_no_commitment_created(self):
        atomic_json(self.md / 'catalog.json', CATALOG)
        self.write_inventory(inventory())
        def changing(*args, **kwargs):
            if args[3] == 'r3-restock':
                self.write_inventory(dict(inventory(), restock={'open': False}))
            return fixture_choose(*args, **kwargs)
        self.assertEqual(plan_restock(self.md, now=NOW, decide_fn=changing), {'buy': []})
        self.assertFalse((self.md / 'restock_commitments.json').exists())
