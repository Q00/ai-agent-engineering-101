"""Signed agreement -> A2A wire -> actual kit receipt handling -> ACP checkout type gate.

Characterization against the real scripts/market_kit.py (loaded read-only, no bytecode written): the A2A data Part
turns the seller's int price into a double (9025 -> 9025.0); the kit restores integral floats to int before
verify_seller (market_kit.py:736-737) and AcpShop.create refuses a non-int price. One authentic captured receipt
(public signature + registered pubkey) and one seller_r3-signed receipt (ephemeral key, no PEM from disk) are checked.
"""
import asyncio
import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = next((p/'scripts'/'market_kit.py' for p in HERE.parents if (p/'scripts'/'market_kit.py').is_file()), None)
FIXTURE = HERE/'fixture_authentic_receipt_20261112.json'


def load_kit():
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True   # never write into the read-only scripts/
    try:
        spec = importlib.util.spec_from_file_location('market_kit_readonly', KIT)
        kit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kit)
        return kit
    finally:
        sys.dont_write_bytecode = old


def kit_normalize(receipt):
    """market_kit.py:736-737, verbatim logic (inline in handle_order, not a callable)."""
    if isinstance(receipt, dict) and isinstance(receipt.get("price"), float) and receipt["price"].is_integer():
        receipt["price"] = int(receipt["price"])
    return receipt


def wire(receipt, task_id):
    """The task as the buyer sees it: the receipt through the real A2A data Part JSON encoding."""
    from a2a.helpers import new_data_part
    from google.protobuf.json_format import MessageToDict
    part = MessageToDict(new_data_part(receipt))
    return {'id': task_id, 'status': {'state': 'TASK_STATE_COMPLETED'}, 'artifacts': [{'parts': [part]}]}


@unittest.skipUnless(KIT is not None, 'scripts/market_kit.py not found above the candidate')
class KitReceiptRoundTrip(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kit = load_kit()

    def test_authentic_captured_receipt_needs_kit_normalization(self):
        import buyer_r3
        fx = json.loads(FIXTURE.read_text(encoding='utf-8'))
        rec = fx['receipt']
        task = wire(rec, rec['task'])
        got = self.kit.receipt_from_task(task)
        self.assertIs(type(got['price']), float)                               # the wire double, as captured
        self.assertFalse(self.kit.verify_seller(fx['pubkey'], dict(got)))      # 9317.0 canonicalizes differently
        self.assertTrue(self.kit.verify_seller(fx['pubkey'], kit_normalize(dict(got))))
        self.assertEqual(kit_normalize(dict(got))['price'], fx['settled']['amount'])
        # our buyer guard hands the kit the same int copy, verified against the registered key
        data = task['artifacts'][0]['parts'][0]['data']
        mine = buyer_r3.valid_receipt(data, fx['pubkey'], rec['item'], rec['task'], 10**9, rec['price'])
        self.assertIs(type(mine['price']), int)
        self.assertTrue(self.kit.verify_seller(fx['pubkey'], mine))

    def test_buyer_guard_rejects_bool_fraction_nonfinite_prices(self):
        import buyer_r3
        fx = json.loads(FIXTURE.read_text(encoding='utf-8'))
        rec = fx['receipt']
        for bad in (True, 9317.5, float('nan'), float('inf'), '9317', None):
            with self.subTest(price=bad):
                self.assertIsNone(buyer_r3.valid_receipt(dict(rec, price=bad), fx['pubkey'], rec['item'], rec['task'],
                                                         10**9, 10**9))
                self.assertFalse(self.kit.verify_seller(fx['pubkey'], dict(rec, price=bad)))

    def test_seller_signed_receipt_survives_wire_and_acp_type_gate(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        import seller_r3
        key = Ed25519PrivateKey.generate()
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'shop_key.pem').write_bytes(key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            os.chdir(root)
            try:
                receipt = seller_r3.sign({'shop': seller_r3.NAME, 'item': 'G2b7e0fe3', 'price': 9025,
                                          'task': 'task-1', 'unit': 'u-1', 'cert': True, 'warranty_hours': 0})
            finally:
                os.chdir(cwd)
        self.assertIs(type(receipt['price']), int)
        pub = self.kit.pub_b64(key)
        got = self.kit.receipt_from_task(wire(receipt, 'task-1'))
        self.assertIs(type(got['price']), float)
        rec = kit_normalize(dict(got))
        self.assertTrue(self.kit.verify_seller(pub, rec))

        shop = self.kit.AcpShop(key, market=None)
        shop.pick_unit = lambda r, acceptable: ({'unit': 'u-1', 'sku': 'G2b7e0fe3', 'cert': 'c'}, None)
        create = lambda r: asyncio.run(shop.create({'line_items': [{'id': 'G2b7e0fe3', 'quantity': 1}], 'receipt': r},
                                                   'https://shop.example'))
        self.assertEqual(create(dict(got)).status_code, 400)                  # the raw double is refused
        ok = create(rec)
        self.assertEqual(ok.status_code, 201)
        body = json.loads(ok.body)
        self.assertEqual((body['totals'][0]['amount'], body['task'], body['unit']), (9025, 'task-1', 'u-1'))
        self.assertTrue(math.isfinite(body['totals'][0]['amount']))


if __name__ == '__main__':
    unittest.main()
