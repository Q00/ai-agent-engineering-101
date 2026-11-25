"""Offline linkage regressions; inputs are synthetic and locally generated."""
import json
from pathlib import Path
import tempfile
import unittest

from r3_memory import Memory, packed


class MemoryLinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.md = Path(temp.name)
        self.memory = Memory(self.md)
        self.addCleanup(self.memory.close)

    def negotiation(self, order='o1', shop='Stable Shop', url='https://old.example',
                    sku='S1', receipt_item='category'):
        self.memory.record('order_seen', order, dict(order=order, item='category', acceptable=['S1', 'S2']))
        self.memory.record('negotiation', order, dict(
            order=order, shop=shop, shop_url=url, sku=sku, task_id='task-'+order,
            outcome='receipt_pending', receipt=dict(item=receipt_item, price=10)))

    def payment(self, paid=False, **extra):
        row = dict(order='o1', shop='https://old.example/', item='category', price=10, paid=paid)
        row.update(extra)
        self.memory.record('payment', row['order'], row)
        return row

    def settlement(self, ledger=1, shop='Stable Shop', sku='S1', name='Product', observed=1000):
        self.memory.record('settlement', str(ledger), dict(
            order='o1', shop=shop, shop_url='https://old.example', sku=sku,
            observed_at=observed, settled=dict(name=name, ledger=ledger, sku=sku, value=20)))

    def broken(self, name='Product', at=1100):
        (self.md / 'broken.jsonl').write_text(json.dumps(dict(name=name, broke_at=at))+'\n')
        self.memory.reconcile()

    def context(self, shop='Stable Shop', sku='S1'):
        return self.memory.context(shop=shop, sku=sku, role='buyer')

    def test_rejection_linked_by_order_and_original_url_not_current_url(self):
        self.negotiation()
        raw = self.payment(reason='Shelf life below requirement')
        context = self.context()
        event = next(e for e in context['recent'] if e['kind'] == 'payment')
        self.assertEqual(event['data']['outcome'], 'payment_rejected')
        self.assertEqual(event['data']['shop_name'], 'Stable Shop')
        self.assertEqual(event['data']['reason'], raw['reason'])
        self.assertEqual(context['pending_count'], 0)
        self.assertFalse(any(e['data'].get('outcome') == 'receipt_pending' for e in context['recent']))
        self.assertEqual(self.memory.get('payment', 'o1')['data'], raw)
        self.assertEqual(self.memory.get('negotiation', 'o1')['data']['outcome'], 'receipt_pending')

    def test_payment_first_is_linked_when_negotiation_arrives_later(self):
        self.payment()
        self.assertEqual(self.context()['recent'], [])
        self.negotiation()
        self.assertIn('payment', [e['kind'] for e in self.context()['recent']])

    def test_wrong_shop_url_never_inherits_shop_name(self):
        self.negotiation()
        self.payment(shop='https://unrelated.example')
        self.assertNotIn('payment', [e['kind'] for e in self.context()['recent']])

    def test_explicit_acceptable_receipt_sku_takes_precedence_over_selected_sku(self):
        self.negotiation(receipt_item='S2')
        self.payment(item='S2')
        context = self.context(sku='S2')
        event = next(e for e in context['recent'] if e['kind'] == 'payment')
        self.assertEqual(event['data']['sku_basis'], 'negotiated_not_delivery_proof')

    def test_pending_explicit_sku_is_not_credited_to_selected_sku(self):
        self.negotiation(receipt_item='S2')
        self.assertEqual(self.context(sku='S1')['pending_count'], 0)
        self.assertEqual(self.context(sku='S2')['pending_count'], 1)

    def test_actual_settlement_overrides_intended_sku(self):
        self.negotiation()
        self.payment(True, ledger=1)
        self.settlement(sku='S2')
        context = self.context(sku='S2')
        event = next(e for e in context['recent'] if e['kind'] == 'payment')
        self.assertEqual(event['data']['sku_basis'], 'actual_settlement')
        self.assertEqual(event['data']['outcome'], 'settled_paid')
        self.assertEqual(self.context(sku='S1')['recent'], [])
        self.assertEqual(self.memory.get('negotiation', 'o1')['data']['sku'], 'S1')

    def test_unique_breakage_is_explicitly_inferred_not_rewritten_fact(self):
        self.settlement()
        self.broken()
        event = next(e for e in self.context()['recent'] if e['kind'] == 'broken')
        self.assertEqual(event['data']['attribution'], 'inferred_unique_name_time')
        self.assertIs(event['data']['attribution_is_inferred'], True)
        self.assertEqual(event['data']['ledger'], 1)
        self.assertEqual(self.memory.events('broken')[0]['data']['attribution'], 'unresolved_name_only')
        self.assertEqual(self.memory.get('settlement', '1')['data']['settled']['value'], 20)

    def test_late_second_candidate_withdraws_inferred_shop_attribution(self):
        self.settlement()
        self.broken()
        self.assertIn('broken', [e['kind'] for e in self.context()['recent']])
        self.settlement(ledger=2, shop='Other Shop')
        self.assertNotIn('broken', [e['kind'] for e in self.context()['recent']])
        event = next(e for e in self.memory.context(role='buyer')['recent'] if e['kind'] == 'broken')
        self.assertEqual(event['data']['candidate_count'], 2)
        self.assertEqual(event['data']['attribution'], 'unresolved_name_only')

    def test_breakage_before_purchase_and_after_window_are_not_attributed(self):
        self.settlement()
        for stamp in (999, 1000+6*3600+1):
            self.broken(at=stamp)
        self.assertNotIn('broken', [e['kind'] for e in self.context()['recent']])

    def test_late_settlement_links_already_consumed_breakage(self):
        self.broken()
        self.assertNotIn('broken', [e['kind'] for e in self.context()['recent']])
        self.settlement()
        self.assertIn('broken', [e['kind'] for e in self.context()['recent']])

    def test_scoped_link_survives_many_unrelated_events(self):
        self.negotiation()
        self.payment()
        for i in range(600):
            self.memory.record('order_seen', str(i), {})
        self.assertIn('payment', [e['kind'] for e in self.context()['recent']])

    def test_private_failure_reason_does_not_leak_to_seller(self):
        self.negotiation()
        self.payment(reason='budget 987654321')
        context = self.memory.context(sku='S1', role='seller')
        self.assertNotIn('987654321', packed(context))
        self.assertNotIn('payment', [e['kind'] for e in context['recent']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
