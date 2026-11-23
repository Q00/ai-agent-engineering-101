"""Run with docker run --rm -i --network none IMAGE python - < this file.

Uses disposable container state and generated test keys; no market/model calls.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

sys.path.insert(0, '/opt/deploy')
import launch

data = Path('/data')
assert os.getuid() == 1000, 'The workload must run as the non-root node user'
assert not list(Path('/opt').rglob('*.pem')), 'No key may be baked into the image'
assert not (data / 'shop_key.pem').exists(), 'Use a disposable container without host mounts'
assert not (data / '.market/market-owner').exists()
try:
    launch.require_identity()
except ValueError as error:
    assert 'Missing shop_key.pem' in str(error)
else:
    raise AssertionError('Missing imported identity must block market startup')
print('PASS: non-root workload, image contains no keys, missing identity blocks startup')

for program in (['python', '--version'], ['claude', '--version'], ['cloudflared', '--version']):
    print(subprocess.check_output(program, text=True).strip())

keys = {}
for name in ('shop_key.pem', 'buyer_key.pem'):
    key = Ed25519PrivateKey.generate()
    content = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    (data / name).write_bytes(content)
    (data / name).chmod(0o600)
    keys[name] = content
preserved = {'buyer-memory.json': '[]\n', 'seller-memory.jsonl': '',
             'logs/market-kit.log': 'historical fixture log\n',
             '.market/market-owner': 'synthetic-claim-for-local-check\n'}
for name, text in preserved.items():
    (data / name).write_text(text)
launch.prepare()
for name, content in keys.items():
    assert (data / name).read_bytes() == content
for name, content in preserved.items():
    assert (data / name).read_text() == content
config = json.loads((data / 'agent.json').read_text())
for section in ('seller', 'buyer', 'market_buyer'):
    assert config[section]['cmd'].startswith('/opt/venv/bin/python ')
    assert '/Users/' not in config[section]['cmd']
print('PASS: startup refreshes portable commands while preserving keys, memories, logs and claim code')

with (data / 'logs/local-seller-check.log').open('w') as output:
    seller = subprocess.Popen([sys.executable, '/data/seller.py', '--port', '9011'],
                              stdout=output, stderr=subprocess.STDOUT)
    try:
        origin = 'http://127.0.0.1:9011'
        with httpx.Client(timeout=5) as client:
            for _ in range(100):
                if seller.poll() is not None:
                    raise RuntimeError((data / 'logs/local-seller-check.log').read_text())
                try:
                    response = client.get(origin + '/.well-known/agent-card.json')
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError('Local seller did not start')
            assert response.json()['version'] == '1.11.0'
            guarantee = client.get(origin + '/.well-known/quality-guarantee.json').json()
            assert guarantee['final_prices'] == {'lamp': 105, 'chair': 185, 'bookshelf': 270}
            key = Ed25519PrivateKey.from_private_bytes(
                __import__('cryptography.hazmat.primitives.serialization', fromlist=['load_pem_private_key'])
                .load_pem_private_key(keys['shop_key.pem'], None).private_bytes_raw())
            public = key.public_key()
            assert base64.b64decode(guarantee['verification']['public_key_base64']) == public.public_bytes_raw()

            def send(text, task_id=None):
                message = {'role': 'ROLE_USER', 'messageId': str(time.monotonic_ns()), 'parts': [{'text': text}]}
                if task_id:
                    message['taskId'] = task_id
                result = client.post(origin + '/', headers={'A2A-Version': '1.0'},
                    json={'jsonrpc': '2.0', 'id': str(time.monotonic_ns()),
                          'method': 'SendMessage', 'params': {'message': message}})
                result.raise_for_status()
                result = result.json()
                assert 'error' not in result, result
                return result['result'].get('task', result['result'])

            for item, price in guarantee['final_prices'].items():
                quote = send(f'I want to buy a {item} for {price + 50}')
                assert quote['status']['state'] == 'TASK_STATE_INPUT_REQUIRED'
                assert quote['status']['message']['metadata']['ask'] == price
                final = send('I accept', quote['id'])
                assert final['status']['state'] == 'TASK_STATE_COMPLETED'
                receipt = next(p['data'] for a in final['artifacts'] for p in a['parts'] if 'data' in p)
                assert receipt['shop'] == 'psyoung82' and receipt['item'] == item
                assert receipt['task'] == quote['id'] and receipt['price'] == price
                body = {k: receipt[k] for k in ('shop', 'item', 'price', 'task')}
                body['price'] = int(body['price'])
                canon = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
                public.verify(base64.b64decode(receipt['sig_seller']), canon(body))
                try:
                    public.verify(base64.b64decode(receipt['sig_seller']), canon(dict(body, price=price + 1)))
                except InvalidSignature:
                    pass
                else:
                    raise AssertionError('Tampered price must not verify')
                print(f'PASS: real local A2A {item} checkout at {price}, signed receipt, tamper rejection')
    finally:
        seller.terminate()
        seller.wait(timeout=10)
print('PASS: local container integration complete; no market registration or model request')
