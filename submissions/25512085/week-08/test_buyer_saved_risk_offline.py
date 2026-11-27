"""Step2 synthetic policy fixtures, not observed ranking quality."""
import unittest
import ast
import base64
import copy
import json
from pathlib import Path
import time
from unittest.mock import patch

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import market_buyer as buyer
import store
import test_buyer_price_offline as fixtures

NOW = 1800000000


class SavedRiskTests(unittest.TestCase):
    def test_formula_full_short_and_missing(self):
        for grade, q in buyer.BREAK_RATE.items():
            for hours in (0, 1, 5.99, 6, 72, None):
                with self.subTest(grade=grade, hours=hours):
                    expected = (1-q)*.4 - (0 if hours is not None and hours >= 6 else q)
                    self.assertAlmostEqual(buyer.goods_expected_saved(600, 1000, grade, hours), expected)

    def test_c_is_not_blacklisted(self):
        self.assertLess(buyer.goods_expected_saved(600, 1000, 'C', 0), 0)
        self.assertGreater(buyer.goods_expected_saved(400, 1000, 'C', 0), 0)

    def test_zero_score_gate_is_strict(self):
        with patch.dict(buyer.BREAK_RATE, {'C': .5}):
            self.assertEqual(buyer.goods_expected_saved(0.5, 1, 'C', 0), -.25)
            self.assertEqual(buyer.goods_expected_saved(1, 1, 'C', 6), 0)
            with self.assertRaises(ValueError):
                buyer.build_accept_policy({'kind': 'goods'}, 1, 1, 6, 'C')

    def test_invalid_terms_fail_closed(self):
        for value in (True, False, '6', float('nan'), float('inf'), -1, [], {}):
            with self.subTest(value=value):
                self.assertIsNone(buyer.goods_expected_saved(600, 1000, 'A', value))
        for grade in (True, [], {}, 1):
            self.assertIsNone(buyer.goods_expected_saved(600, 1000, grade, 6))
        for grade in (None, 'unknown', 'a'):
            self.assertEqual(buyer.goods_expected_saved(600, 1000, grade, 6),
                             buyer.goods_expected_saved(600, 1000, 'C', 6))
        for amount, lp in ((True, 1000), (600, False), (float('nan'), 1000), (600, 0)):
            self.assertIsNone(buyer.goods_expected_saved(amount, lp, 'A', 6))

    def test_checkout_whitelist_never_weakens_grade(self):
        order = {'kind': 'goods'}
        for grade in ('A', 'B', 'C'):
            policy = buyer.build_accept_policy(order, 400, 1000, 6, grade)
            self.assertEqual(policy['grades'], list('ABC'[:'ABC'.index(grade)+1]))
            self.assertEqual(policy['warranty_hours'], 6)
            self.assertTrue(policy['cert'])
            self.assertLessEqual(set(policy), {'cert', 'grades', 'warranty_hours'})
        self.assertEqual(buyer.build_accept_policy(order, 600, 1000, 0, 'B')['grades'], ['A', 'B'])
        self.assertEqual(buyer.build_accept_policy(order, 600, 1000, 1, 'B')['warranty_hours'], 1)


class ReceiptRiskTests(unittest.TestCase):
    setUp = fixtures.BuyerPriceTests.setUp
    receipt = fixtures.BuyerPriceTests.receipt
    run_buyer = fixtures.BuyerPriceTests.run_buyer

    def test_actual_receipt_terms_and_sku(self):
        self.order['kind'] = 'goods'
        self.catalog[self.sku]['list_price'] = 100000
        self.catalog['f00000002']['list_price'] = 50000
        for turn in range(4):
            for price, hours, grade, accepted in ((30000, 0, 'C', False), (20000, 0, 'C', True),
                                                  (30000, 1, 'C', False), (30000, 6, 'C', True),
                                                  (30000, float('nan'), 'A', False),
                                                  (30000, True, 'A', False), (30000, 6, [], False)):
                with self.subTest(turn=turn, price=price, hours=hours, grade=grade):
                    rec = self.receipt(price, warranty_hours=hours, claim=grade)
                    rec['item'] = 'f00000002'
                    result, _, _, _ = self.run_buyer(ask=price, receipt=rec, complete_after=turn, budget=200000)
                    self.assertEqual(result['receipt'] is not None, accepted)


class ConditionRankingTests(unittest.TestCase):
    def rows(self, **changes):
        return [dict(dict(ts=NOW-i, amount=800, grade='A', warranty_hours=6,
                          shop='shop', order=str(i), seq=i), **changes) for i in range(3)]

    def proxy(self, rows, name='shop'):
        return buyer.goods_ranking_proxy({'demand_radar': {'sku_comparables': {'sku': rows}}},
                                         'sku', name, 'https://shop.invalid', 1000, 800, NOW)

    def test_fresh_condition_vs_unknown_fallback(self):
        self.assertGreater(self.proxy(self.rows()), self.proxy([]))
        self.assertGreater(self.proxy(self.rows()), self.proxy(self.rows(warranty_hours=0, grade='C')))

    def test_missing_stale_sparse_wrong_shop_are_unknown(self):
        unknown = self.proxy([])
        for rows in (self.rows(ts=NOW-10801), self.rows(ts=NOW+1), self.rows()[:2],
                     self.rows(shop='other'), self.rows(warranty_hours=None),
                     self.rows(grade=True), self.rows(warranty_hours=float('nan'))):
            self.assertEqual(self.proxy(rows), unknown)

    def test_mixed_conditions_do_not_pool_samples(self):
        rows = self.rows()[:2] + self.rows(grade='B')[:1]
        self.assertEqual(self.proxy(rows), self.proxy([]))

    def test_metadata_roundtrip_bounded(self):
        rows = self.rows()
        hub = {'demand_radar': {'sku_comparables': {'sku': rows}}}
        store.compact_hub(hub, NOW)
        expected = [{k: v for k, v in r.items() if k not in ('order', 'seq')} for r in rows]
        self.assertEqual(hub['demand_radar']['sku_comparables']['sku'], expected)
        self.assertEqual(self.proxy(hub['demand_radar']['sku_comparables']['sku']), self.proxy(rows))

    def test_shop_metadata_global_cap_and_complete_recent_groups(self):
        original = {str(i): self.rows(ts=NOW-i*3, shop='shop-'+str(i)) for i in range(150)}
        hub = {'demand_radar': {'sku_comparables': copy.deepcopy(original)}}
        store.compact_hub(hub, NOW)
        compact = hub['demand_radar']['sku_comparables']
        self.assertEqual(sum('shop' in r for rows in compact.values() for r in rows), 120)
        self.assertEqual({sku for sku, rows in compact.items() if any('shop' in r for r in rows)},
                         {str(i) for i in range(40)})
        for sku, rows in compact.items():
            self.assertEqual([{k: v for k, v in r.items() if k not in ('shop', 'order', 'seq')} for r in rows],
                             [{k: v for k, v in r.items() if k not in ('shop', 'order', 'seq')}
                              for r in original[sku]])

    def test_metadata_selection_is_idempotent_and_mapping_order_independent(self):
        original = {str(i): self.rows(ts=NOW-i*3, shop='shop-'+str(i)) for i in range(150)}
        left = {'demand_radar': {'sku_comparables': copy.deepcopy(original)}}
        right = {'demand_radar': {'sku_comparables': dict(reversed(list(original.items())))}}
        store.compact_hub(left, NOW)
        store.compact_hub(right, NOW)
        self.assertEqual(left, right)
        once = copy.deepcopy(left)
        store.compact_hub(left, NOW)
        self.assertEqual(left, once)

    def test_only_latest_three_per_shop_sku_condition_keep_metadata(self):
        rows = [dict(ts=NOW-i, amount=800+i, grade='A', warranty_hours=6, shop='shop')
                for i in range(10)]
        hub = {'demand_radar': {'sku_comparables': {'sku': rows}}}
        store.compact_hub(hub, NOW)
        rows = hub['demand_radar']['sku_comparables']['sku']
        self.assertEqual([r['ts'] for r in rows if 'shop' in r], [NOW, NOW-1, NOW-2])
        self.assertEqual(len(rows), 10)  # core evidence is not trimmed for metadata

    def test_invalid_or_oversized_shop_never_survives_compaction(self):
        for shop in (None, True, {}, [], '', 'x'*129, '한'*43, 'shop\nname', ' shop', '\ud800'):
            with self.subTest(shop=repr(shop)):
                hub = {'demand_radar': {'sku_comparables': {'sku': self.rows(shop=shop)}}}
                store.compact_hub(hub, NOW)
                self.assertTrue(all('shop' not in r for r in hub['demand_radar']['sku_comparables']['sku']))

    def test_sparse_metadata_can_accumulate_without_displacing_qualified_groups(self):
        hub = {'demand_radar': {'sku_comparables': {'sku': self.rows()[:2]}}}
        store.compact_hub(hub, NOW)
        rows = hub['demand_radar']['sku_comparables']['sku']
        self.assertEqual(sum('shop' in r for r in rows), 2)
        rows.append(self.rows()[2])
        store.compact_hub(hub, NOW)
        self.assertGreater(self.proxy(rows), self.proxy([]))

    def test_ledger_ingestion_also_bounds_tags_and_replay_stays_deduplicated(self):
        hub = store.default_hub()
        events = [dict(seq=i*3+j+1, kind='settle', ts=NOW-i,
                       data=dict(sku='sku-'+str(i), shop='shop', amount=800,
                                 grade='A', warranty_hours=6, buyer='outside', order=str(i*3+j)))
                  for i in range(150) for j in range(3)]
        with patch.object(store.time, 'time', return_value=NOW):
            store.apply_ledger_rows(hub, events, {})
            once = copy.deepcopy(hub)
            store.apply_ledger_rows(hub, events, {})
        rows = [r for rows in hub['demand_radar']['sku_comparables'].values() for r in rows]
        self.assertLessEqual(sum('shop' in r for r in rows), 120)
        self.assertTrue(all('seq' not in r and 'order' not in r for r in rows))
        self.assertEqual(hub, once)

    def test_price_shrink_uses_sample_count_not_confidence_claim(self):
        rows = self.rows(amount=600)
        # Three observations receive 1/2 price weight against the 800 SKU prior.
        self.assertAlmostEqual(self.proxy(rows), .97*.3)
        self.assertGreater(self.proxy(rows + self.rows(amount=600, seq=99)), self.proxy(rows))

    def test_inclusive_three_hour_boundary_and_missing_warranty(self):
        self.assertGreater(self.proxy(self.rows(ts=NOW-10800)), self.proxy([]))
        rows = self.rows()
        for row in rows:
            del row['warranty_hours']
        self.assertEqual(self.proxy(rows), self.proxy([]))

    def test_ranking_uses_shop_condition_not_offer_claim(self):
        shops = [dict(name=name, url='https://'+name+'.invalid', offers=[{'sku': 'sku', 'grade': 'A'}])
                 for name in ('unknown', 'shop')]
        hub = {'demand_radar': {'sku_comparables': {'sku': self.rows()}}}
        with patch.object(buyer, 'fetch_board_stats', return_value={}), \
                patch.object(buyer, 'check_acp_support', return_value=True), \
                patch.object(buyer.time, 'time', return_value=NOW):
            chosen, _, _ = buyer.pick_round3_shop_and_sku(shops, {}, hub, {'sku': {'list_price': 1000}},
                                                        {'kind': 'goods', 'acceptable': ['sku']}, 2000)
        self.assertEqual(chosen['name'], 'shop')


class KitAcceptContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Execute just the real read-only kit predicates, not its CLI/server.
        source = Path(__file__).resolve().parents[3] / 'scripts/market_kit.py'
        parsed = ast.parse(source.read_text(encoding='utf-8'))
        functions = [n for n in parsed.body if isinstance(n, ast.FunctionDef)
                     and n.name in ('canon', 'check_accept')]
        namespace = dict(json=json, base64=base64, time=time,
                         Ed25519PublicKey=Ed25519PublicKey, InvalidSignature=InvalidSignature)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
        cls.check = staticmethod(namespace['check_accept'])
        cls.canon = staticmethod(namespace['canon'])

    def checkout(self, grade='A', warranty=6):
        key = Ed25519PrivateKey.generate()  # isolated ephemeral fixture, never a real key
        body = {'unit': 'unit', 'sku': 'sku', 'grade': grade}
        order = {'kind': 'goods', 'market_pubkey': base64.b64encode(
            key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()}
        co = {'unit': 'unit', 'item': 'sku', 'warranty_hours': warranty,
              'cert': {'cert': body, 'sig': base64.b64encode(key.sign(self.canon(body))).decode()}}
        return order, co

    def test_actual_certificate_not_receipt_grade_controls_checkout(self):
        policy = buyer.build_accept_policy({'kind': 'goods'}, 400, 1000, 6, 'A')
        order, co = self.checkout(grade='B')
        self.assertIn('grade B', self.check(order, co, policy))
        order, co = self.checkout(grade='A')
        self.assertIsNone(self.check(order, co, policy))

    def test_full_window_cannot_be_weakened_at_checkout(self):
        policy = buyer.build_accept_policy({'kind': 'goods'}, 600, 1000, 6, 'C')
        for warranty in (0, 1, 5.99):
            order, co = self.checkout(grade='C', warranty=warranty)
            self.assertIn('warranty', self.check(order, co, policy))

    def test_no_cert_rejects_and_no_unsupported_accept_keys(self):
        policy = buyer.build_accept_policy({'kind': 'goods'}, 600, 1000, 0, 'B')
        order, co = self.checkout(grade='B', warranty=0)
        self.assertIsNone(self.check(order, co, policy))
        co.pop('cert')
        self.assertIsNotNone(self.check(order, co, policy))
        self.assertEqual(set(policy), {'cert', 'grades'})


if __name__ == '__main__':
    unittest.main()
