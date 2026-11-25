import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from buyer_policy import acceptance, market_saved_reference, paid_price_samples, purchase_price_guidance
from buyer_r3 import followup_offer, opening_request
from ops import buyer_service
import market_buyer_r3


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

    def test_saved_reference_is_strict_and_separate_from_market_prices(self):
        baseline = {'saved': .6, 'observed_at': 1000}
        cost = {'unit_cost': 50000, 'estimated': False}
        prices = (71000, 73000, 74000)
        guidance = purchase_price_guidance(80000, prices, list_price=100000, saved_baseline=baseline, cost_reference=cost)
        self.assertEqual(guidance['suggested_maximum'], 74000)
        self.assertEqual(guidance['saved_reference']['max_price_to_improve_if_usable'], 69999)
        sparse = purchase_price_guidance(80000, prices[:1], list_price=100000, saved_baseline=baseline, cost_reference=cost)
        self.assertIsNone(sparse['suggested_maximum'])
        self.assertEqual(sparse['saved_reference'], guidance['saved_reference'])
        kwargs = {'message': 'asking 75000', 'last_offer': 65000, 'cap': 80000, 'sku': 'F1',
                  'list_price': 100000, 'memory': {'price_guidance': guidance},
                  'deadline': time.monotonic() + 20}
        self.assertEqual(followup_offer(**kwargs, offers_remaining=2), 67499)
        self.assertEqual(followup_offer(**kwargs, offers_remaining=1), 75000)

    def test_negotiation_prompt_uses_margin_cost_scenario(self):
        kwargs={'message':'asking 75000','last_offer':65000,'cap':80000,'sku':'G1',
                'list_price':100000,'memory':{'price_guidance':{
                    'cost_reference':{'unit_cost':50000,'estimated':True}}},
                'deadline':time.monotonic()+150}
        with patch('buyer_r3.decide_json',return_value={
                'action':'offer','price':75000,'reason':'valid margin scenario'}) as model:
            self.assertEqual(followup_offer(**kwargs),75000)
        prompt=model.call_args.args[1]
        self.assertEqual(prompt['saved_if_usable']['at_last_offer'],.7)
        self.assertEqual(prompt['saved_if_usable']['at_seller_ask'],.5)
        self.assertEqual(prompt['saved_if_usable']['at_cap'],.4)
        self.assertTrue(prompt['saved_if_usable']['cost_reference']['estimated'])
        self.assertIn('(list_price-paid)/(list_price-unit_cost)',prompt['objective'])

    def test_saved_reference_uses_recent_cache_during_board_failure(self):
        board = {'round': 3, 'buyers': [{'name': 'buyer', 'deals': 10, 'saved': .2733}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'saved.json'
            with patch('buyer_policy.urlopen', return_value=io.StringIO(json.dumps(board))) as fetch:
                baseline = market_saved_reference('buyer', path=path, now=1000)
                self.assertEqual(baseline, {'saved': .2733, 'observed_at': 1000,'deals':10})
                self.assertEqual(market_saved_reference('buyer', path=path, now=1299), baseline)
                fetch.assert_called_once()
            with patch('buyer_policy.urlopen', side_effect=TimeoutError):
                self.assertEqual(market_saved_reference('buyer', path=path, now=1300), baseline)
                self.assertIsNone(market_saved_reference('buyer', path=path, now=1901))

    def test_pre_patch_saved_cache_is_not_reused_as_margin_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'saved.json'
            path.write_text(json.dumps({'name':'buyer','saved':.27,
                                        'observed_at':1000,'checked_at':1000}))
            with patch('buyer_policy.urlopen', side_effect=TimeoutError):
                self.assertIsNone(market_saved_reference('buyer',path=path,now=1001))

    def test_missing_unit_cost_does_not_silently_use_list_discount(self):
        guidance = purchase_price_guidance(80000, (60000,65000,70000),
                                          list_price=100000,
                                          saved_baseline={'saved':.68,'observed_at':1000})
        self.assertNotIn('saved_reference',guidance)
        self.assertNotIn('saved_if_usable_at_reference',guidance)
        self.assertEqual(guidance['median_paid'],65000)

    def test_candidate_prices_are_shop_specific_and_ranked_by_observed_saving(self):
        now=time.time()
        history=[{'event':'settled','at':now-10,'sku':'G1','grade':'A','ledger':index,
                  'price':price,'shop_name':name,'shop':f'https://{name}'}
                 for index,(name,price) in enumerate(
                     [('expensive',700)]*3+[('cheap',500)]*3,1)]
        shops=[{'name':name,'url':f'https://{name}','offers':[{'sku':'G1','qty':1}]}
               for name in ('expensive','cheap')]
        order={'order':'o1','kind':'goods','budget':1000,'acceptable':['G1']}
        catalog={'G1':{'kind':'goods','list_price':1000}}
        with tempfile.TemporaryDirectory() as directory:
            previous=Path.cwd()
            os.chdir(directory)
            try:
                with patch.object(market_buyer_r3,'read_events',return_value=history):
                    choices=market_buyer_r3.candidates(order,shops,catalog,'own')
                self.assertEqual([c['shop'] for c in choices],['cheap','expensive'])
                self.assertEqual(choices[0]['price_guidance']['reference_source'],'same_shop_sku')
                self.assertEqual(choices[0]['price_guidance']['median_paid'],500)
                self.assertEqual(choices[1]['price_guidance']['median_paid'],700)
            finally:
                os.chdir(previous)

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
