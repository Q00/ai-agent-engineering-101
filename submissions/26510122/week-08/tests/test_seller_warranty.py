import asyncio
import base64
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
import test_seller_persistence as fixtures
import seller_r3
import seller_warranty as warranty
from seller_state import SellerState
from newsvendor_tool import quote_candidates
from replenishment_plan import inventory_risk
from market_memory import events


class WarrantyContractTests(unittest.TestCase):
    tearDown = fixtures.PersistenceTests.tearDown
    write_inventory = fixtures.PersistenceTests.write_inventory
    send = fixtures.PersistenceTests.send
    client = fixtures.PersistenceTests.client

    def setUp(self):
        fixtures.PersistenceTests.setUp(self)
        self.unit.update(kind='goods',expires_at=None,grade='A',name='Example Goods')
        self.write_inventory()

    def answers(self, ask=900):
        return [{'action':'ask','price':ask,'unit_id':'u1','reason':'quote'},
                {'action':'accept','price':None,'unit_id':'u1','reason':'agree'}]

    def test_required_six_hour_warranty_survives_restart_and_reaches_receipt(self):
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1, grade A, warranty 6 h required.')
                self.assertEqual(first['status']['state'],'TASK_STATE_INPUT_REQUIRED')
            async with self.client() as c:
                final=await self.send(c,'800',first['id'])
            self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
            receipt=SellerState().pending_receipt(first['id'])
            self.assertEqual(receipt['warranty_hours'],6);self.assertEqual(receipt['price'],800)
            key=serialization.load_pem_private_key(Path('shop_key.pem').read_bytes(),password=None)
            key.public_key().verify(base64.b64decode(receipt['sig_seller']),seller_r3.canon({k:receipt[k] for k in ('shop','item','price','task')}))
            self.assertEqual(next(e for e in events() if e['event']=='receipt_signed')['warranty_hours'],6)
        with patch.object(seller_r3,'decide_json',side_effect=self.answers()) as model:
            asyncio.run(flow())
        self.assertEqual(model.call_args.args[1]['agreed_warranty_hours'],6)
        self.assertTrue(any('Warranty 6 h included' in e.get('text','') for e in events()))

    def test_longer_coverage_is_not_promised(self):
        async def flow():
            async with self.client() as c:
                result=await self.send(c,'I want F1, warranty 24 h required.')
            self.assertEqual(result['status']['state'],'TASK_STATE_REJECTED')
        with patch.object(seller_r3,'decide_json',side_effect=AssertionError('unsupported coverage')):
            asyncio.run(flow())

    def test_food_does_not_inherit_goods_coverage(self):
        self.unit.update(kind='food',expires_at=self.now+100*3600);self.write_inventory()
        async def flow():
            async with self.client() as c:
                rejected=await self.send(c,'I want F1, warranty 6 h required.')
                self.assertEqual(rejected['status']['state'],'TASK_STATE_REJECTED')
                first=await self.send(c,'I want F1')
                await self.send(c,'800',first['id'])
            self.assertNotIn('warranty_hours',SellerState().pending_receipt(first['id']))
        with patch.object(seller_r3,'decide_json',side_effect=self.answers()):asyncio.run(flow())

    def test_refund_floor_cannot_be_bypassed_by_model_or_discount(self):
        fixtures.PersistenceTests.enable_promotion(self)
        async def flow():
            async with self.client() as c:
                first=await self.send(c,'I want F1, warranty 6 h required.')
                deal=SellerState().restore_deals()[first['id']];floor=deal['warranty_price_floor']
                self.assertGreaterEqual(deal['ask'],floor)
                low=await self.send(c,'500',first['id'])
                self.assertEqual(low['status']['state'],'TASK_STATE_INPUT_REQUIRED')
                self.assertIsNone(SellerState().pending_receipt(first['id']))
                final=await self.send(c,str(floor),first['id'])
                self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
                receipt=SellerState().pending_receipt(first['id'])
                self.assertEqual(receipt['price'],floor);self.assertEqual(receipt['warranty_hours'],6)
        answers=self.answers(ask=500)+[self.answers()[1]]
        with patch.object(seller_r3,'decide_json',side_effect=answers):asyncio.run(flow())

    def test_legacy_negotiation_keeps_original_no_warranty_terms(self):
        async def flow():
            async with self.client() as c:first=await self.send(c,'I want F1')
            state=SellerState();deal=state.restore_deals()[first['id']]
            del deal['warranty_hours'];del deal['warranty_price_floor'];state.save_deal(first['id'],deal,create=False)
            async with self.client() as c:final=await self.send(c,'800',first['id'])
            self.assertEqual(final['status']['state'],'TASK_STATE_COMPLETED')
            self.assertNotIn('warranty_hours',state.pending_receipt(first['id']))
        with patch.object(seller_r3,'decide_json',side_effect=self.answers()):asyncio.run(flow())


class WarrantyEconomicsTests(unittest.TestCase):
    def test_candidates_show_refund_scenarios_not_invented_market_probabilities(self):
        unit={'sku':'G1','unit':'u1','grade':'A','kind':'goods','cost':900,'list_price':1000,'supplied_at':100}
        quote=quote_candidates(unit,now=100,warranty_hours=6)
        self.assertEqual(quote['warranty']['price_floor'],1001)
        self.assertIn('unpublished',quote['warranty']['risk_source'])
        for q in quote['candidates']:
            self.assertGreaterEqual(q['price'],1001)
            self.assertGreater(q['warranty_risk']['refund_scenarios'][2]['margin_after_refunds_and_accrued_holding'],0)

    def test_cash_exposure_counts_only_distinct_paid_unexpired_covered_sales(self):
        receipt={'event':'receipt_signed','task_id':'t1','unit':'u1','warranty_hours':6}
        sale={'event':'settled','task_id':'t1','unit':'u1','ledger':1,'price':1000,'at':100}
        unpaid={**receipt,'task_id':'unpaid','unit':'unpaid'}
        old={**sale,'task_id':'old','ledger':2}
        rows=[receipt,unpaid,sale,sale,old]
        self.assertEqual(warranty.exposure(rows,200)['cash_reserve'],100)
        self.assertEqual(warranty.exposure(rows,200)['covered_sales'],1)
        self.assertEqual(warranty.exposure(rows,100+6*3600)['cash_reserve'],0)
        with patch('replenishment_plan.events',return_value=rows):
            risk=inventory_risk({'balance':1000000,'units':[]},200,pending_cost=0)
        self.assertEqual(risk['warranty_exposure']['cash_reserve'],100)
        self.assertEqual(risk['cash_reserve'],600100)
