import asyncio
import base64
import json
from pathlib import Path
import socket
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from r3.adapter import load_kit, install
from r3.state import Journal, atomic_json, policy
from r3.protocol import canon, signed_body
from r3.model import Chooser
from seller_r3 import Seller, build
from market_buyer import buy
from review import review


def unit(sku='G111', uid='u1', **kwargs):
    return dict(unit=uid, sku=sku, name='one product', category='goods>appliance', list_price=10000,
                kind='goods', cost=6000, grade='A', supplied_at=time.time() - 10, expires_at=None, cert=None, **kwargs)


def pub(key):
    return base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def receipt(key, task='t', sku='G111', price=8000):
    rec = {'shop': 'Seochanit Shop', 'item': sku, 'price': price, 'task': task}
    rec['sig_seller'] = base64.b64encode(key.sign(canon(signed_body(rec)))).decode()
    return rec


def test_actual_a2a_server_and_buyer_interoperate(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    j = Journal(tmp_path / '.market')
    u = unit()
    atomic_json(j.md / 'inventory.json', {'round': 3, 'balance': 5000000, 'units': [u]})
    atomic_json(j.md / 'catalog.json', {'G111': {'name': u['name'], 'category': u['category'], 'list_price': 10000, 'kind': 'goods'}})
    key = Ed25519PrivateKey.generate()
    keypath = tmp_path / 'synthetic.pem'
    keypath.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(build(port, Seller(j, key_path=keypath)), log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        atomic_json(j.md / 'registry.json', {url: {'name': 'shop', 'pubkey': pub(key)}})
        order = {'order': 'o1', 'acceptable': ['G111'], 'budget': 10000, 'kind': 'goods', 'expires': time.time() + 300}
        out = buy(order, [{'name': 'shop', 'url': url, 'offers': [{'sku': 'G111', 'qty': 1}]}], j)
        assert out['receipt'] and out['receipt']['item'] == 'G111'
        assert out['receipt']['price'] <= order['budget']
        assert out['accept']['expected_sku'] == 'G111'
        assert out['accept']['warranty_hours'] == 6
        assert j.events('agreement')
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        sock.close()


def test_adapter_prevents_acceptable_but_wrong_sku(tmp_path):
    j = Journal(tmp_path)
    kit = install(load_kit(), j)
    reason = kit.check_accept({'acceptable': ['G111', 'G222'], 'kind': 'goods'},
                             {'item': 'G222'}, {'expected_sku': 'G111'})
    assert 'SKU' in reason


def test_adapter_refuses_weak_food_acceptance(tmp_path):
    kit = install(load_kit(), Journal(tmp_path))
    reason = kit.check_accept({'acceptable': ['G111'], 'kind': 'food', 'use_within_hours': 3},
                             {'item': 'G111'}, {'expected_sku': 'G111', 'min_hours_left': 1})
    assert 'weaker' in reason


def test_exact_unit_choice_and_concurrent_checkout_exclusion(tmp_path):
    j = Journal(tmp_path)
    atomic_json(j.md / 'catalog.json', {'G111': {}, 'G222': {}})
    kit = install(load_kit(), j)
    key = Ed25519PrivateKey.generate()
    # A competing acceptable unit sorts earlier under the unmodified default.
    first, other = unit(), unit(sku='G222', uid='u2')
    first['supplied_at'], other['supplied_at'] = 100, 0
    class Market:
        def call(self, *args, **kwargs):
            return httpx.Response(200, json={'round': 3, 'units': [first, other]})
    shop = kit.AcpShop(key, Market(), j.md)
    def body(task):
        return {'receipt': receipt(key, task), 'line_items': [{'id': 'G111', 'quantity': 1}], 'acceptable': ['G111', 'G222']}
    async def exercise():
        return await asyncio.gather(shop.create(body('t1'), 'http://shop'), shop.create(body('t2'), 'http://shop'))
    responses = asyncio.run(exercise())
    assert sorted(r.status_code for r in responses) == [201, 409]
    result = json.loads(next(r for r in responses if r.status_code == 201).body)
    assert result['unit'] == 'u1'


def test_abandoned_checkout_releases_hold(tmp_path):
    j = Journal(tmp_path)
    kit = install(load_kit(), j)
    shop = kit.AcpShop(Ed25519PrivateKey.generate(), None)
    shop.sessions['x'] = {'status': 'ready_for_payment', 'unit': 'u', 'r3_created': time.time() - 1000}
    shop.expire_sessions()
    assert shop.sessions['x']['status'] == 'canceled'


def test_real_certificate_checked_before_food_payment(tmp_path):
    kit = install(load_kit(), Journal(tmp_path))
    key = Ed25519PrivateKey.generate()
    cert = {'unit': 'u', 'sku': 'G111', 'expires_at': time.time() + 3600, 'grade': 'A'}
    co = {'unit': 'u', 'item': 'G111', 'cert': {'cert': cert, 'sig': base64.b64encode(key.sign(canon(cert))).decode()}}
    order = {'acceptable': ['G111'], 'kind': 'food', 'market_pubkey': pub(key), 'use_within_hours': 3}
    reason = kit.check_accept(order, co, {'expected_sku': 'G111', 'cert': True, 'min_hours_left': 3})
    assert 'shelf life' in reason


def test_settlement_review_is_idempotent_and_honest(tmp_path):
    j = Journal(tmp_path)
    atomic_json(j.md / 'catalog.json', {'G111': {'kind': 'goods', 'list_price': 10000}})
    deal = {'order': {'order': 'o', 'kind': 'goods'}, 'receipt': {'price': 8000},
            'settled': {'sku': 'G111', 'shop': 'http://s', 'unit': 'u', 'value': 10000, 'grade': 'A', 'ledger': 5}}
    result = review(deal, j)
    review(deal, j)
    assert len(j.events('purchase')) == 1
    assert 'no grade claim' in result['review']
    assert len(j.pending_posts()) == 1


def test_bad_receipt_never_produces_purchase(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    j = Journal(tmp_path)
    atomic_json(j.md / 'catalog.json', {'G111': {'kind': 'goods', 'list_price': 10000}})
    key = Ed25519PrivateKey.generate()
    atomic_json(j.md / 'registry.json', {'http://shop': {'pubkey': pub(key)}})
    class BadPeer:
        def discover(self, url): pass
        def send(self, text, task=None):
            return {'id': 't', 'status': {'state': 'TASK_STATE_COMPLETED'},
                    'artifacts': [{'parts': [{'data': receipt(key, sku='G222')}]}]}
        def close(self): pass
    result = buy({'order': 'o', 'acceptable': ['G111'], 'budget': 10000, 'expires': time.time() + 300},
                 [{'name': 'bad', 'url': 'http://shop', 'offers': [{'sku': 'G111', 'qty': 1}]}], j, peer_factory=BadPeer)
    assert result['receipt'] is None
    assert not j.events('agreement')
