import json
from pathlib import Path
import tempfile
import time
import unittest

from buyer_policy import acceptance, paid_price_samples, purchase_price_guidance
from buyer_r3 import followup_offer, opening_request
from ops import buyer_service


class PlainBuyerTests(unittest.TestCase):
    def test_paid_prices_match_sku_and_order_shelf_life(self):
        order = {'kind': 'food', 'acceptable': ['F1'], 'use_within_hours': 48}
        now = 400_000
        sale = {'event': 'settled', 'at': now - 1, 'sku': 'F1', 'hours_left_at_recording': 72}
        rows = [{**sale, 'ledger': n, 'price': price}
                for n, price in enumerate((510, 550, 600), 1)]
        rows += [rows[1], {**sale, 'ledger': 4, 'price': 100, 'hours_left_at_recording': 24},
                 {**sale, 'ledger': 5, 'price': 100, 'event': 'payment'},
                 {**sale, 'ledger': 6, 'price': 100, 'sku': 'F2'},
                 {**sale, 'ledger': 7, 'price': 100, 'at': now - 73*3600}]
        samples = paid_price_samples(order, rows, now)['F1']
        self.assertEqual(samples, (510, 550, 600))
        result = purchase_price_guidance(850, samples)
        self.assertEqual(result['opening_offer_range'], [506, 550])
        self.assertEqual(result['suggested_maximum'], 600)
        self.assertEqual(purchase_price_guidance(525, samples)['suggested_maximum'], 525)
        self.assertEqual(purchase_price_guidance(850, samples[:2])['opening_offer_range'], [595, 722])

    def test_buyer_counter_uses_soft_ceiling_then_preserves_authorized_final_offer(self):
        kwargs = {'message': 'asking 700', 'last_offer': 550, 'cap': 850, 'sku': 'F1',
                  'list_price': 1000, 'memory': {'price_guidance': {'suggested_maximum': 600}},
                  'deadline': time.monotonic() + 20}
        self.assertEqual(followup_offer(**kwargs, offers_remaining=2), 575)
        self.assertEqual(followup_offer(**kwargs, offers_remaining=1), 700)

    def test_food_message_matches_checkout_requirement_without_budget_or_roleplay(self):
        order = {'kind': 'food', 'use_within_hours': 360, 'budget': 98765}
        message = opening_request('Coffee', 'C1', order)
        self.assertIn(f"{acceptance(order)['min_hours_left']:g} hours", message)
        self.assertIn('market-signed unit certificate', message)
        self.assertNotIn('98765', message)
        self.assertNotIn('hunger', message)

    def test_goods_grade_is_required_and_warranty_is_optional(self):
        message = opening_request('Chair', 'G1', {'kind': 'goods'})
        self.assertIn('Only grade A or B', message)
        self.assertIn('preferred, not required', message)

    def test_frozen_buyer_contains_no_roleplay_modules_or_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); keys = root/'keys'; keys.mkdir()
            for name in ('shop_key.pem', 'buyer_key.pem'): (keys/name).write_text('test fixture only')
            runtime = root/'runtime'
            buyer_service.prepare(runtime, keys)
            manifest = buyer_service.validate(runtime)
            self.assertEqual(manifest['mode'], 'plain')
            self.assertFalse((runtime/'release/buyer_life.py').exists())
            config = json.loads((runtime/'agent.json').read_text())
            self.assertEqual(set(config), {'harness', 'market_buyer', 'review'})
            config['market_buyer']['cmd'] += ' --life'
            (runtime/'agent.json').write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, 'Roleplay'): buyer_service.validate(runtime)

    def test_release_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); keys = root/'keys'; keys.mkdir()
            for name in ('shop_key.pem', 'buyer_key.pem'): (keys/name).write_text('test fixture only')
            runtime = root/'runtime'; buyer_service.prepare(runtime, keys)
            (runtime/'release/buyer_r3.py').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'integrity'): buyer_service.validate(runtime)


if __name__ == '__main__': unittest.main()
