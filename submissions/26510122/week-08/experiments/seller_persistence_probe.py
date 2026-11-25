"""Real model + two local seller rebuilds; no real supply, ACP or buyer actions."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from unittest.mock import patch
import uuid

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import seller_r3
from seller_state import SellerState


async def scenario():
    calls = []
    real_model = seller_r3.decide_json
    def model(*args):
        calls.append(args[1]['phase'])
        return real_model(*args)
    async def send(client, text, task=None):
        message = {'role': 'ROLE_USER', 'messageId': str(uuid.uuid4()), 'parts': [{'text': text}]}
        if task:
            message['taskId'] = task
        response = await client.post('/', json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage',
                                               'params': {'message': message}}, headers={'A2A-Version': '1.0'})
        result = response.json()
        assert 'error' not in result, result
        return result['result'].get('task', result['result'])
    def client():
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=seller_r3.build(9999)), base_url='http://testserver')
    with patch.object(seller_r3, 'decide_json', side_effect=model), \
         patch.object(seller_r3.counterparty_memory, 'refresh', return_value=[]):
        async with client() as c:
            first = await send(c, 'I want F1. It must last 84 hours, grade A or B.')
        ask = int(re.search(r'asking (\d+)', first['status']['message']['parts'][0]['text']).group(1))
        async with client() as c:
            last = await send(c, f'{max(900, ask)}, but only if it lasts 84 hours', first['id'])
        async with client() as c:
            other = await send(c, 'I want F1')
    state = SellerState()
    return {'real_model_calls': len(calls), 'model_phases': calls,
            'server_rebuilds': 2, 'resumed_task_state': last['status']['state'],
            'competing_request_state': other['status']['state'],
            'saved_requirements_after_receipt': state.requirements_for(first['id']),
            'receipt_leases': len([r for r in state.active_leases() if r[2] == 'receipt']),
            'real_market_writes': 0, 'temporary_key_only': True}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); output = args.output.resolve(); output.mkdir(exist_ok=False)
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='seller-db-probe-', dir=ROOT/'state') as directory:
        os.chdir(directory)
        md = Path('.market'); md.mkdir(); now = time.time()
        unit = {'unit': 'u1', 'sku': 'F1', 'name': 'Example Food', 'kind': 'food', 'category': 'food',
                'grade': 'B', 'cost': 500, 'list_price': 1000, 'expires_at': now+100*3600, 'supplied_at': now}
        (md/'inventory.json').write_text(json.dumps({'round': 3, 'units': [unit]}))
        (md/'catalog.json').write_text(json.dumps({'F1': {'name': 'Example Food', 'kind': 'food', 'list_price': 1000, 'shelf_hours': 200}}))
        key = Ed25519PrivateKey.generate()
        Path('shop_key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()))
        try:
            result = asyncio.run(scenario())
            if Path('logs/shop-events.jsonl').exists():
                (output/'shop-events.jsonl').write_bytes(Path('logs/shop-events.jsonl').read_bytes())
        finally:
            os.chdir(previous)
    (output/'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    assert result['resumed_task_state'] == 'TASK_STATE_COMPLETED'
    assert result['competing_request_state'] == 'TASK_STATE_REJECTED'
    assert result['saved_requirements_after_receipt']['min_hours_left'] == 84
    assert result['receipt_leases'] == 1


if __name__ == '__main__':
    main()
