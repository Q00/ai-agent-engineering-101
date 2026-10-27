"""Offline cryptography and policy tests using ephemeral, non-deployment keys."""
import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.testclient import TestClient

from seller import (ask_message, build, canon, load_signing_key, opening_ceiling,
                    sign_receipt, validate_market_seller)


class Signing(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.body = dict(shop='Test Shop', item='lamp', price=120, task='task-1')

    def test_exact_fields_and_no_input_mutation(self):
        result = sign_receipt(self.body, key=self.key)
        self.assertEqual(set(result), set(self.body) | {'sig_seller'})
        self.assertNotIn('sig_seller', self.body)
        self.key.public_key().verify(base64.b64decode(result['sig_seller']), canon(self.body))

    def test_every_signed_field_tamper_rejected(self):
        signature = base64.b64decode(sign_receipt(self.body, key=self.key)['sig_seller'])
        for field, value in dict(shop='Other Shop', item='chair', price=121, task='another-task').items():
            with self.subTest(field=field), self.assertRaises(InvalidSignature):
                self.key.public_key().verify(signature, canon(dict(self.body, **{field: value})))

    def test_wrong_public_key_rejected(self):
        signature = base64.b64decode(sign_receipt(self.body, key=self.key)['sig_seller'])
        with self.assertRaises(InvalidSignature):
            Ed25519PrivateKey.generate().public_key().verify(signature, canon(self.body))

    def test_transport_float_requires_integer_normalization(self):
        result = sign_receipt(self.body, key=self.key)
        received = dict(self.body, price=120.0)
        signature = base64.b64decode(result['sig_seller'])
        with self.assertRaises(InvalidSignature):
            self.key.public_key().verify(signature, canon(received))
        received['price'] = int(received['price'])
        self.key.public_key().verify(signature, canon(received))

    def test_rejects_invalid_body(self):
        bad = [dict(self.body, price=p) for p in (True, 120.0, -1)]
        bad += [dict(self.body, task=''), dict(self.body, fee=1)]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                sign_receipt(body, key=self.key)

    def test_canonical_order_whitespace_utf8(self):
        self.assertEqual(canon(dict(b=2, a=1)), b'{"a":1,"b":2}')
        self.assertEqual(canon(dict(shop='\uac00\uac8c')), '{"shop":"\uac00\uac8c"}'.encode('utf-8'))

    def test_missing_key_does_not_generate_replacement(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'missing.pem'
            with self.assertRaises(FileNotFoundError):
                sign_receipt(self.body, key_path=path)
            self.assertFalse(path.exists())

    def test_existing_key_loading_and_wrong_type(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fixture.pem'
            for key, allowed in ((self.key, True), (ec.generate_private_key(ec.SECP256R1()), False)):
                encoded = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption())
                path.write_bytes(encoded)
                if allowed:
                    signed = sign_receipt(self.body, key_path=path)
                    self.key.public_key().verify(base64.b64decode(signed['sig_seller']), canon(self.body))
                else:
                    with self.assertRaises(ValueError):
                        load_signing_key(path)
                self.assertEqual(path.read_bytes(), encoded)


class Policy(unittest.TestCase):
    product = dict(id='lamp', name='lamp', reserve=100)

    def action(self, kind, price):
        return dict(action=kind, item_id='lamp', price=price, summary='offline fixture')

    def test_opening_price_ceiling(self):
        self.assertEqual([opening_ceiling(v) for v in (100, 180, 260)], [110, 198, 286])
        self.assertEqual(opening_ceiling(101), 111)
        self.assertLessEqual(10 * opening_ceiling(101), 11 * 101)
        validate_market_seller(self.action('ask', 110), [self.product], None, None, None)
        with self.assertRaises(ValueError):
            validate_market_seller(self.action('ask', 125), [self.product], None, None, None)

    def test_reserve_remains_authoritative(self):
        for kind in ('ask', 'accept'):
            with self.assertRaises(ValueError):
                validate_market_seller(self.action(kind, 99), [self.product], 'lamp', 99, 110)

    def test_counteroffer_never_increases(self):
        with self.assertRaises(ValueError):
            validate_market_seller(self.action('ask', 110), [self.product], 'lamp', 90, 105)

    def test_honor_quoted_price(self):
        for kind, price in (('reject', None), ('ask', 110)):
            with self.assertRaises(ValueError):
                validate_market_seller(self.action(kind, price), [self.product], 'lamp', 110, 110)
        validate_market_seller(self.action('accept', 110), [self.product], 'lamp', 110, 110)

    def test_counter_below_current_offer_rejected(self):
        with self.assertRaises(ValueError):
            validate_market_seller(self.action('ask', 100), [self.product], 'lamp', 105, 110)

    def test_final_floor_is_visible_and_last_number(self):
        self.assertEqual(ask_message(self.product, 100), 'lamp; final minimum price 100')


class SignedProtocol(unittest.TestCase):
    @staticmethod
    def decision(system, state, validator, *args):
        return validator(dict(action='ask' if state['item_id'] is None else 'accept', item_id='lamp',
                              price=110 if state['item_id'] is None else state['incoming_offer'], summary='offline fixture'))

    @staticmethod
    def send(client, text, task_id=None):
        message = dict(role='ROLE_USER', messageId=uuid.uuid4().hex, parts=[dict(text=text)])
        if task_id:
            message['taskId'] = task_id
        return client.post('/', headers={'A2A-Version': '1.0'}, json=dict(jsonrpc='2.0', id=uuid.uuid4().hex,
                           method='SendMessage', params=dict(message=message))).json()['result']['task']

    def test_real_sdk_receipt_verifies(self):
        key = Ed25519PrivateKey.generate()
        with contextlib.redirect_stdout(io.StringIO()), TestClient(build(9998, decision=self.decision, signing_key=key)) as client:
            first = self.send(client, 'lamp')
            last = self.send(client, '100', first['id'])
        self.assertEqual(last['status']['state'], 'TASK_STATE_COMPLETED')
        received = last['artifacts'][0]['parts'][0]['data']
        self.assertEqual(received['task'], first['id'])
        self.assertEqual(set(received), {'shop', 'item', 'price', 'task', 'sig_seller'})
        body = {k: received[k] for k in ('shop', 'item', 'price', 'task')}
        body['price'] = int(body['price'])
        key.public_key().verify(base64.b64decode(received['sig_seller']), canon(body))

    def test_signing_failure_never_emits_unsigned_receipt(self):
        key = Ed25519PrivateKey.generate()
        with contextlib.redirect_stdout(io.StringIO()), TestClient(build(9997, decision=self.decision, signing_key=key)) as client:
            first = self.send(client, 'lamp')
            with patch('seller.sign_receipt', side_effect=ValueError('simulated signing failure')):
                last = self.send(client, '100', first['id'])
        self.assertEqual(last['status']['state'], 'TASK_STATE_FAILED')
        self.assertFalse(last.get('artifacts'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
