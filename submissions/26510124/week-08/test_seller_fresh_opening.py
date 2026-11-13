"""Only recent measured price references bypass the opening model call."""
import json
import time
import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from seller_agent import DEFAULT, build
from test_adaptive_seller import send
import test_closing as fixtures


class FreshOpeningTests(unittest.TestCase):
    setUp = fixtures.ClosingTests.setUp
    seller = fixtures.ClosingTests.seller

    def test_fresh_quote_and_receipt_keep_floor_and_identity(self):
        seller, _ = self.seller()
        reference = {'target':80000, 'source':'own_settled_sales', 'created_at':time.time()}
        with patch.object(seller.openings, 'for_product', return_value=reference), \
                patch('seller_r3.inventory_decision', side_effect=AssertionError('no model')) as model, \
                TestClient(build(DEFAULT, seller)) as client:
            first = send(client, 'G00001')
            ask = first['status']['message']['metadata']['ask']
            self.assertEqual(ask, 80000)
            done = send(client, str(ask), first['id'])
            self.assertTrue(done['artifacts'])
        model.assert_not_called()
        rows = [json.loads(line) for line in seller.events.read_text().splitlines()]
        quote = next(r for r in rows if r['outcome'] == 'quote')
        self.assertEqual(quote['decision_source'], 'fresh_evidence_opening')
        self.assertGreaterEqual(quote['first_response_seconds'], 0)
        self.assertEqual(rows[-1]['first_response_seconds'], quote['first_response_seconds'])

    def test_stale_future_manual_and_missing_evidence_keep_model(self):
        for ref in (None, {'source':'own_settled_sales','created_at':time.time()-3601},
                    {'source':'comparable_market_sales','created_at':time.time()+600},
                    {'source':'manual_pilot_policy','created_at':time.time()}):
            seller, _ = self.seller()
            reference = {**ref, 'target':80000} if ref else None
            with patch.object(seller.openings, 'for_product', return_value=reference), \
                    patch('seller_r3.inventory_decision', return_value=fixtures.counter(80000)) as model, \
                    TestClient(build(DEFAULT, seller)) as client:
                send(client, 'G00001')
            self.assertEqual(model.call_count, 1)
