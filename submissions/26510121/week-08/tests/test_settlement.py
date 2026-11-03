"""Real HTTP + course ACP/AP2 client + real Ed25519, against a local synthetic market."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from r3.adapter import load_kit, install
from r3.state import Journal, atomic_json
from seller_r3 import Seller, build
from market_buyer import buy
from review import review
from test_market_contract import unit, pub


def test_end_to_end_payment_moves_credits_once(tmp_path, monkeypatch):
    if not os.environ.get('R3_LIVE_INTEGRATION'):
        monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    else:
        monkeypatch.delenv('R3_MODEL_MODE', raising=False)
    j = Journal(tmp_path / '.market')
    kit = install(load_kit(), j)
    shop_key, buyer_key, market_key = (Ed25519PrivateKey.generate() for _ in range(3))
    product = {'name': 'synthetic appliance', 'category': 'goods>appliance', 'kind': 'goods', 'list_price': 10000}
    atomic_json(j.md / 'catalog.json', {'G111': product})
    u = unit()
    u['name'] = product['name']
    cert = {'unit': 'u1', 'sku': 'G111', 'grade': 'A', 'expires_at': None}
    u['cert'] = {'cert': cert, 'sig': base64.b64encode(market_key.sign(kit.canon(cert))).decode()}
    inventory = {'round': 3, 'balance': 4994000, 'start': 5000000, 'injected': 0, 'units': [u]}
    atomic_json(j.md / 'inventory.json', inventory)
    keypath = tmp_path / 'test-shop.pem'
    keypath.write_bytes(shop_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    order = {'order': 'synthetic-order', 'round': 3, 'item': 'appliance', 'kind': 'goods', 'budget': 10000,
             'acceptable': ['G111'], 'expires': time.time() + 300, 'market_pubkey': pub(market_key),
             'mandates': {'checkout': {'mandate': {'scope': 'synthetic-checkout'}},
                          'payment': {'mandate': {'constraints': [{'type': 'payment.amount_range', 'max': 10000}]}}}}

    class SyntheticMarket:
        balance = 4994000
        settlements = 0
        buyer_paid = 0
        def call(self, method, path, role='shop', **kwargs):
            if path == '/api/inventory':
                return httpx.Response(200, json=dict(inventory, balance=self.balance, units=[] if self.settlements else [u]))
            assert path == '/api/settle'
            if self.settlements:
                return httpx.Response(409, json={'checks': {'not_settled_before': False}})
            data = kwargs['json']
            co = kit.jwt_payload(data['checkout_jwt'], pub(shop_key))
            assert co and co['item'] == 'G111' and co['unit'] == 'u1'
            assert co['price'] <= order['budget'] and time.time() < order['expires']
            checkout_hash = kit.b64url(hashlib.sha256(data['checkout_jwt'].encode()).digest())
            for key, vct, open_kind in [('checkout_mandate', 'mandate.checkout.1', 'checkout'),
                                        ('payment_mandate', 'mandate.payment.1', 'payment')]:
                signed = data[key]
                buyer_key.public_key().verify(base64.b64decode(signed['sig']), kit.canon(signed['mandate']))
                assert signed['mandate']['vct'] == vct
                assert signed['mandate']['order'] == order['order']
                assert signed['mandate']['checkout_hash'] == checkout_hash
                assert signed['mandate']['open_mandate'] == hashlib.sha256(kit.canon(order['mandates'][open_kind]['mandate'])).hexdigest()
            pm = data['payment_mandate']['mandate']
            assert pm['payee'] == url and pm['amount'] == co['price']
            self.balance += co['price']
            self.buyer_paid += co['price']
            self.settlements += 1
            mr = {'shop': url, 'unit': 'u1', 'sku': 'G111', 'name': u['name'], 'grade': 'A', 'claim': 'A',
                  'claim_honest': True, 'warranty_hours': co['warranty_hours'], 'value': 10000, 'ledger': 101}
            return httpx.Response(200, json={'mandate_receipt': mr, 'ledger': 101})

    market = SyntheticMarket()
    acp = kit.AcpShop(shop_key, market, j.md)
    async def endpoint(request):
        return await acp.handle(request, request.url.path.strip('/'), await request.body(), url)
    seller_app = build(port, Seller(j, key_path=keypath))
    app = Starlette(routes=list(seller_app.routes) + [
        Route('/.well-known/acp.json', endpoint), Route('/acp/{path:path}', endpoint, methods=['GET', 'POST'])])
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        atomic_json(j.md / 'registry.json', {url: {'name': 'shop', 'pubkey': pub(shop_key)}})
        result = buy(order, [{'name': 'shop', 'url': url, 'offers': [{'sku': 'G111', 'qty': 1}]}], j)
        assert result['receipt']
        rec = result['receipt']
        # Emulate the kit Proxy's authoritative record of what the seller sent.
        acp.terms[rec['task']] = dict(rec)
        mr = kit.acp_pay(order, url, pub(shop_key), rec, buyer_key, result['accept'])
        assert mr and market.settlements == 1
        assert market.balance == 4994000 + rec['price'] and market.buyer_paid == rec['price']
        rv = review({'order': order, 'receipt': rec, 'settled': mr}, j)
        assert rv['stars'] >= 1 and len(j.events('purchase')) == 1
        assert kit.acp_pay(order, url, pub(shop_key), rec, buyer_key, result['accept']) is None
        assert market.settlements == 1
        if os.environ.get('R3_LIVE_INTEGRATION'):
            assert j.events('model_usage'), 'A numeric fallback alone is not a live-model validation'
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        sock.close()
