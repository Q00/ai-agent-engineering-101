"""Offline estimates, fallback negotiations, forum evidence and read-only diagnostics."""
import contextlib
import io
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock

from r3_common import DEFAULT_POLICY, atomic_json
from r3_controller import forum_drafts
from r3_memory import Memory
from r3_seller import pricing
from r3_status import inspect_state
from r3_strategy import buyer_estimate, buyer_fallback, price_ratio, selection_fallback


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)
        self.now = time.time()
        self.memory = Memory(self.md)
        self.addCleanup(self.memory.close)
        self.catalog = {'sku': {'list_price': 100, 'category': 'goods', 'kind': 'goods'}}
        atomic_json(self.md / 'catalog.json', self.catalog)

    def settlement(self, ledger, *, age=7*3600, shop='shop1', price=70):
        self.memory.record('settlement', ledger,
            {'shop': shop, 'shop_url': 'https://offline.example', 'sku': 'sku', 'budget': 987654,
             'observed_at': self.now - age, 'receipt': {'price': price},
             'settled': {'ledger': ledger, 'sku': 'sku', 'name': str(ledger), 'claim_honest': True}},
            ts=self.now - age)

    def test_price_learning_needs_three_public_settled_samples(self):
        for seq in range(1, 4):
            self.memory.record('market_settle', seq,
                {'seq': seq, 'item': 'sku', 'amount': 80, 'source': 'public_ledger'}, ts=self.now - seq)
        info = price_ratio(self.memory, self.catalog, 'goods', self.now)
        self.assertEqual(info['median'], .8)
        self.assertEqual(info['samples'], 3)
        self.assertIsNone(price_ratio(self.memory, self.catalog, 'other', self.now)['median'])

    def test_price_ceiling_uses_list_price_and_preserves_previous_quote(self):
        unit = dict(cost=60, list_price=100, kind='goods', supplied_at=self.now)
        normal = pricing(unit, DEFAULT_POLICY, self.now)
        self.assertEqual(normal['ceiling'], 90)
        learned = pricing(unit, DEFAULT_POLICY, self.now, market_prices={'median': .8, 'samples': 30})
        self.assertLess(learned['ceiling'], normal['ceiling'])
        self.assertEqual(pricing(unit, DEFAULT_POLICY, self.now, previous_ask=65)['ceiling'], 65)

    def test_inferred_breaks_count_only_after_followup_and_same_shop_sku(self):
        self.settlement(1)
        self.settlement(2, age=100)
        self.settlement(3, shop='other')
        self.memory.record('broken', 'b', {'name': '1', 'broke_at': self.now - 2*3600}, ts=self.now)
        candidate = dict(shop_name='shop1', sku='sku', kind='goods', list_price=100)
        estimate = buyer_estimate(self.memory, candidate, self.now)
        self.assertEqual(estimate['mature_samples'], 1)
        self.assertEqual(estimate['inferred_breaks'], 1)
        self.assertAlmostEqual(estimate['break_risk_estimate'], 2/11, places=4)
        self.assertIn('inferred', estimate['assumptions'])

    def test_higher_list_price_is_not_automatically_selected(self):
        rows = [dict(shop_name='expensive', sku='sku1', list_price=1000,
                     value_estimate={'expected_value': 200, 'estimated_price': 300}),
                dict(shop_name='useful', sku='sku2', list_price=500,
                     value_estimate={'expected_value': 450, 'estimated_price': 300})]
        self.assertEqual(selection_fallback(rows, 350)['shop_name'], 'useful')

    def test_buyer_fallback_never_exceeds_budget_or_value_and_stops(self):
        state = dict(budget=80, list_price=200, current_ask=999, last_offer=None, rounds=0,
                     value_estimate={'expected_value': 70})
        for i in range(8):
            action = buyer_fallback(state)
            if action['action'] == 'walk':
                break
            self.assertLessEqual(action['price'], 66)
            state.update(last_offer=action['price'], rounds=i + 1)
        self.assertEqual(action['action'], 'walk')

    def test_aggregate_refs_real_records_and_no_private_budget(self):
        for seq in (1, 2, 3):
            self.settlement(seq)
        drafts = forum_drafts(self.memory)
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]['refs'], [1, 2, 3])
        self.assertIn('0.700', drafts[0]['post'])
        self.assertNotIn('987654', drafts[0]['post'])
        self.memory.record('forum_queued', drafts[0]['id'], drafts[0])
        self.settlement(4)
        self.assertEqual(forum_drafts(self.memory), [])

    def test_status_is_read_only_and_distinguishes_public_and_own_records(self):
        self.settlement(1)
        self.memory.record('market_settle', 99, {'seq': 99, 'item': 'sku', 'source': 'public_ledger'})
        report = inspect_state(self.md)
        db = report['databases']['r3-memory.sqlite3']
        self.assertEqual(db['public_ledger_max_seq'], 99)
        self.assertEqual(db['events_by_kind']['settlement'], 1)
        missing = self.md / 'does-not-exist'
        inspect_state(missing)
        self.assertFalse(missing.exists())

    def test_buyer_timeout_fallback_preserves_original_receipt_and_no_bad_review(self):
        from test_r3_buyer import BuyerTests
        fixture = BuyerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        atomic_json(fixture.md / 'policy.json', {'fallback_enabled': True})
        with contextlib.redirect_stdout(io.StringIO()):
            result = fixture.execute(decision=Mock(side_effect=TimeoutError('fixture')))
        self.assertEqual(result['receipt'], fixture.received)
        self.assertIsNone(result['stars'])
        self.assertEqual(result['review'], '')

    def test_seller_timeout_fallback_can_quote_and_sell_with_original_guards(self):
        from test_r3_seller import InventorySellerTests, send
        fixture = InventorySellerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write('policy.json', {'fallback_enabled': True, 'adaptive_pricing': True})
        fixture.choose.side_effect = TimeoutError('fixture')
        first = send(fixture.client, 'G00001')
        self.assertEqual(first['status']['state'], 'TASK_STATE_INPUT_REQUIRED')
        final = send(fixture.client, '140', first['id'])
        self.assertEqual(final['status']['state'], 'TASK_STATE_COMPLETED')
        self.assertEqual(final['artifacts'][0]['parts'][0]['data']['price'], 140)
