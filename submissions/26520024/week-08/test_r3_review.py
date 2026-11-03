"""Offline settlement reviews; fixture signatures are not cryptographic proofs."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from r3_memory import Memory
from r3_review import main, run, validate_deal


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.md = Path(self.temp.name)
        self.now = 1800000000
        self.deal = dict(
            order=dict(order='o1', item='milk', category='food>milk', budget=98765,
                       acceptable=['F001'], kind='food', use_within_hours=2,
                       expires=self.now + 300, mandates={'secret': 1}, nonce='private'),
            receipt=dict(shop='Example', item='F001', task='t1', price=100,
                         sig_seller='fixture', unit='u1', cert={'keep': 'all'}),
            settled=dict(shop='http://127.0.0.1:9999', unit='u1', sku='F001', name='Milk',
                         grade='A', claim='A', claim_honest=True, expires_at=self.now + 4 * 3600,
                         warranty_hours=0, value=160, ledger=7))
        self.catalog = {'F001': dict(sku='F001', name='Milk', kind='food', category='food>milk', list_price=150)}
        (self.md / 'catalog.json').write_text(json.dumps(self.catalog), encoding='utf-8')
        (self.md / 'shops.json').write_text(json.dumps([dict(name='Example', url='http://127.0.0.1:9999')]), encoding='utf-8')
        self.write()

    def write(self):
        (self.md / 'deal.json').write_text(json.dumps(self.deal), encoding='utf-8')

    def execute(self, now=None):
        self.write()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = run(self.md / 'deal.json', self.md, self.now if now is None else now)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), result)
        self.assertEqual(set(result), {'stars', 'review'})
        self.assertNotIn('98765', result['review'])
        return result

    def test_cli_locked_before_reading_deal_or_recording_memory(self):
        with patch.dict('os.environ', {'R3_ENABLE_LIVE': '0'}), \
                patch('sys.argv', ['r3_review.py', '--deal', '/missing/deal.json']), \
                patch('r3_review.run') as execute, self.assertRaises(SystemExit):
            main()
        execute.assert_not_called()

    def test_actual_settlement_evidence_and_idempotent_ledger(self):
        result = self.execute()
        self.assertEqual(result['stars'], 5)
        self.assertIn('0.67x catalog list price', result['review'])
        self.assertIn('market grade A', result['review'])
        self.assertEqual(self.execute(now=self.now + 10 * 3600), result)
        events = Memory(self.md).events('settlement')
        self.assertEqual(len(events), 1)
        saved = events[0]['data']
        self.assertEqual(saved['settled'], self.deal['settled'])
        self.assertEqual(saved['receipt'], self.deal['receipt'])
        self.assertEqual(saved['observed_at'], self.now)
        self.assertEqual(saved['shop'], 'Example')
        self.assertNotIn('mandates', saved['order_details'])
        self.assertFalse((self.md / 'outbox.jsonl').exists())

    def test_false_quality_claim_is_evidence_not_automatic_four_stars(self):
        self.deal['settled'].update(grade='C', claim='A', claim_honest=False, value=70)
        result = self.execute()
        self.assertLessEqual(result['stars'], 2)
        self.assertIn('inaccurate quality claim', result['review'])

    def test_insufficient_shelf_life_and_zero_value(self):
        self.deal['settled'].update(expires_at=self.now + 3600, value=0)
        result = self.execute()
        self.assertEqual(result['stars'], 1)
        self.assertIn('shorter than the order use horizon', result['review'])

    def test_grade_b_fair_price_and_no_quality_claim(self):
        self.deal['settled'].update(grade='B', claim=None, claim_honest=None, value=150)
        self.deal['receipt']['price'] = 150
        result = self.execute()
        self.assertEqual(result['stars'], 4)
        self.assertNotIn('quality claim', result['review'])

    def test_missing_quality_evidence_is_not_invented(self):
        self.deal['settled'].update(grade=None, claim_honest=None, expires_at=None, value=None)
        result = self.execute()
        self.assertLessEqual(result['stars'], 3)
        self.assertIn('grade unavailable', result['review'])
        self.assertIn('shelf life unavailable', result['review'])

    def test_unsettled_and_mismatched_callback_rejected(self):
        original = copy.deepcopy(self.deal)
        for field, value in [('ledger', None), ('ledger', True), ('sku', 'F002'),
                             ('unit', 'another'), ('order', 'other'), ('price', 99999)]:
            with self.subTest(field=field):
                self.deal = copy.deepcopy(original)
                self.deal['settled'][field] = value
                with self.assertRaises(ValueError):
                    validate_deal(self.deal)
        with self.assertRaises(ValueError):
            validate_deal(dict(original, settled=None))

    def test_payment_ledger_corroboration(self):
        payment = dict(order='o1', shop=self.deal['settled']['shop'], item='F001',
                       price=100, paid=False, ledger=7)
        path = self.md / 'payments.jsonl'
        path.write_text(json.dumps(payment) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.execute()
        payment['paid'] = True
        path.write_text(json.dumps(payment) + '\n', encoding='utf-8')
        self.assertEqual(self.execute()['stars'], 5)

    def test_ledger_cannot_be_reused_for_another_unit(self):
        self.execute()
        self.deal['receipt']['unit'] = 'u2'
        self.deal['settled']['unit'] = 'u2'
        with self.assertRaises(ValueError):
            self.execute()

    def test_category_receipt_and_payment_item_keep_original_signed_category(self):
        self.deal['receipt']['item'] = 'milk'
        original = copy.deepcopy(self.deal['receipt'])
        payment = dict(order='o1', shop=self.deal['settled']['shop'], item='milk',
                       price=100, paid=True, ledger=7)
        (self.md / 'payments.jsonl').write_text(json.dumps(payment) + '\n', encoding='utf-8')
        self.assertEqual(self.execute()['stars'], 5)
        with Memory(self.md) as memory:
            saved = memory.get('settlement', '7')['data']
        self.assertEqual(saved['sku'], 'F001')
        self.assertEqual(saved['receipt'], original)
        self.assertEqual(self.deal['receipt'], original)

    def test_category_receipt_cannot_settle_an_unacceptable_sku(self):
        self.deal['receipt']['item'] = 'milk'
        self.deal['settled']['sku'] = 'F002'
        with self.assertRaises(ValueError):
            validate_deal(self.deal)

    def test_explicit_sku_receipt_must_match_actual_sku_even_if_both_are_acceptable(self):
        self.deal['order']['acceptable'].append('F002')
        self.deal['settled']['sku'] = 'F002'
        with self.assertRaises(ValueError):
            validate_deal(self.deal)
        self.deal['receipt']['item'] = 'F002'
        self.assertEqual(validate_deal(self.deal)[2]['sku'], 'F002')

    def test_category_receipt_payment_cannot_be_silently_normalized_to_sku(self):
        self.deal['receipt']['item'] = 'milk'
        payment = dict(order='o1', shop=self.deal['settled']['shop'], item='F001',
                       price=100, paid=True, ledger=7)
        (self.md / 'payments.jsonl').write_text(json.dumps(payment) + '\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.execute()

    def test_unrelated_category_or_item_cannot_pass_review(self):
        for signed_item in ('food>milk', 'juice', 'F999', None):
            with self.subTest(signed_item=signed_item):
                self.deal['receipt']['item'] = signed_item
                with self.assertRaises(ValueError):
                    validate_deal(self.deal)


if __name__ == '__main__':
    unittest.main(verbosity=2)
