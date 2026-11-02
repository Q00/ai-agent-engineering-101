"""Offline regression cases for relevant, bounded model memory."""
import json
from pathlib import Path
import tempfile
import unittest

from r3_memory import Memory, packed


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = Memory(Path(self.temp.name))
        self.addCleanup(self.memory.close)

    def pending(self, key, shop='Example', sku='F001', **extra):
        data = dict(shop=shop, sku=sku, order=key, budget=987654321,
                    outcome='receipt_pending', receipt=dict(item=sku, price=100))
        data.update(extra)
        self.memory.record('negotiation', key, data)

    def test_pending_scopes_both_shop_and_sku(self):
        self.pending('wrong-shop', shop='Other')
        self.pending('wrong-sku', sku='G002')
        self.pending('right')
        context = self.memory.context(shop='Example', sku='F001', role='buyer')
        self.assertEqual([e['key'] for e in context['pending']], ['right'])
        self.assertEqual(context['pending_count'], 1)
        self.assertEqual(context['recent'], [])
        self.memory.record('payment', 'right', dict(order='right', paid=False))
        self.assertEqual(self.memory.context(shop='Example', sku='F001', role='buyer')['pending'], [])

    def test_seller_does_not_receive_buyer_budget_or_pending_records(self):
        self.pending('pending')
        self.memory.record('settlement', '7', dict(shop='Example', sku='F001',
            budget=987654321, order_details=dict(budget=987654321),
            summary='private budget 987654321', review=dict(review='private budget 987654321'),
            settled=dict(sku='F001', grade='A', value=130, ledger=7)))
        self.memory.record('seller_action', 't:1', dict(sku='F001', action='ask', price=110))
        context = self.memory.context(sku='F001', role='seller')
        self.assertEqual(context['pending'], [])
        self.assertEqual(context['pending_count'], 0)
        self.assertNotIn('987654321', packed(context))
        self.assertNotIn('negotiation', [e['kind'] for e in context['recent']])
        self.assertIn('settlement', [e['kind'] for e in context['recent']])
        self.assertIn('seller_action', [e['kind'] for e in context['recent']])

    def test_sql_scope_can_find_relevant_evidence_beyond_recent_global_window(self):
        self.memory.record('settlement', 'old', dict(shop='Example', sku='F001', price=100))
        for number in range(510):
            self.memory.record('seller_action', str(number), dict(sku='G002', price=2))
        context = self.memory.context(shop='Example', sku='F001', role='buyer')
        self.assertEqual(context['recent'][0]['key'], 'old')

    def test_context_is_bounded_and_raw_evidence_unchanged(self):
        receipt = dict(item='F001', price=100, sig_seller='S' * 50000,
                       cert=dict(cert='C' * 50000), unknown_nested={'secret': 'keep locally'})
        for number in range(20):
            self.pending(str(number), receipt=receipt, summary='x' * 5000)
        context = self.memory.context(shop='Example', sku='F001', role='buyer', limit=20)
        self.assertLessEqual(len(packed(context)), 6000)
        self.assertNotIn('sig_seller', packed(context))
        self.assertNotIn('unknown_nested', packed(context))
        self.assertEqual(self.memory.get('negotiation', '0')['data']['receipt'], receipt)
        self.assertEqual(context['pending_count'], 20)

    def test_default_projection_for_reflection_omits_private_order_and_prose(self):
        self.memory.record('settlement', '4', dict(shop='Example', sku='F001',
            order_details=dict(budget=987654321), budget=987654321,
            summary='private budget 987654321', review=dict(review='private budget 987654321'),
            settled=dict(sku='F001', grade='B', value=90, ledger=4),
            receipt=dict(item='F001', price=100, sig_seller='private blob')))
        event = self.memory.get('settlement', '4')
        projected = self.memory.prompt_event(event)
        text = json.dumps(projected)
        self.assertNotIn('987654321', text)
        self.assertNotIn('private blob', text)
        self.assertEqual(projected['data']['settled']['grade'], 'B')
        self.assertEqual(projected['data']['receipt']['price'], 100)
        self.assertEqual(event['data']['budget'], 987654321)

    def test_zero_limit_returns_no_model_records(self):
        self.pending('one')
        context = self.memory.context(role='buyer', limit=0)
        self.assertEqual(context['recent'], [])
        self.assertEqual(context['pending'], [])
        self.assertEqual(context['pending_count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
