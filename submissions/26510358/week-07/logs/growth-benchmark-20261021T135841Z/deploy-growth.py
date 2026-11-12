"""Apply the tested seller generation; reuse existing identity, tunnel and kit.

Operator probes request real model quotes, then cancel. No market order,
numeric offer, purchase or review is created by this script.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

import httpx

HERE = Path(__file__).resolve().parent
SUB = HERE.parents[1]
REPO = SUB.parents[2]
sys.path.insert(0, str(SUB))
sys.path.insert(0, str(REPO / 'scripts'))
import market_kit
from protocol import A2AClient, task_metadata
from tracing import Trace

PUBLIC = 'https://wisconsin-dylan-nhs-myspace.trycloudflare.com'
KIT_PID = 3566788
MARKET = 'https://market.ourolabs.ai'
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'public_url': PUBLIC,
          'market_orders_created': False, 'purchases_created': False,
          'operator_quote_and_cancel_only': True, 'steps': []}


def save(name, value):
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))


def identities(board):
    return {role: sorted((row['name'], row['joined']) for row in board[role]
                         if (row.get('owner') or {}).get('login') == 'SUNGMYEONGGI')
            for role in ('shops', 'buyers')}


def key_hashes():
    return {name: hashlib.sha256((SUB / name).read_bytes()).hexdigest()
            for name in ('shop_key.pem', 'buyer_key.pem')}


with httpx.Client(timeout=20, trust_env=False) as client, httpx.Client(
        transport=httpx.HTTPTransport(uds=str(SUB / '.market/shop-service/control.sock')),
        timeout=30) as control:
    try:
        before = client.get(MARKET + '/api/board').raise_for_status().json()
        registry_before = client.get(MARKET + '/api/shops').raise_for_status().json()
        save('deployment-board-before.json', before)
        save('deployment-shops-before.json', registry_before)
        old_service = control.get('http://localhost/').raise_for_status().json()
        assert old_service['public_url'] == PUBLIC
        old_shop = next(row for row in registry_before['shops'] if row['name'] == 'myeonggi')
        assert old_shop['url'] == PUBLIC
        old_keys = key_hashes()
        cmdline = Path(f'/proc/{KIT_PID}/cmdline').read_bytes()
        assert b'market_kit.py' in cmdline and PUBLIC.encode() in cmdline

        after_service = control.post('http://localhost/', json={'action': 'reload'}).raise_for_status().json()
        assert after_service['active_generation'] > old_service['active_generation']
        assert after_service['public_url'] == PUBLIC
        save('seller-reloaded.json', after_service)
        card = client.get(PUBLIC + '/.well-known/agent-card.json').raise_for_status().json()
        save('public-card-deployed.json', card)
        assert card['description'].startswith('Ssadassa Shop by myeonggi. Latest base quotes:')
        assert 'lamp 100, chair 180, bookshelf 260' in card['description']
        assert 'REVIEW10' not in card['description']  # current acquisition phase
        assert len(card['skills']) == 3
        assert all(row['url'].startswith(PUBLIC + '/') for row in card['supportedInterfaces'])

        # Read the running kit's already-authorized claim in memory only.
        # Never copy its environment or print/write its claim or returned token.
        env = dict(row.split(b'=', 1) for row in Path(f'/proc/{KIT_PID}/environ').read_bytes().split(b'\0') if b'=' in row)
        assert (SUB / 'shop_key.pem').exists()
        key = market_kit.load_key(SUB / 'shop_key.pem')
        assert market_kit.pub_b64(key) == old_shop['pubkey']
        market = market_kit.Market(MARKET, env[b'MARKET_OWNER'].decode())
        try:
            joined = market.join('shop', 'myeonggi', key, PUBLIC)
            assert joined['name'] == 'myeonggi'
        finally:
            market.c.close()
        del env, joined, market

        a2a = A2AClient(PUBLIC, Trace('operator-growth-probe', HERE / 'quote-probe'), timeout=50)
        try:
            for label, metadata in (('cheapest-in-town', {'buyerName': 'cheapest-in-town'}),
                                    ('nocha12', {'buyerName': 'nocha12'}), ('anonymous', {})):
                task = a2a.rpc('SendMessage', {'message': {'role': 'ROLE_USER', 'messageId': uuid.uuid4().hex,
                    'parts': [{'text': 'I want to buy a lamp'}],
                    'metadata': {'probePurpose': 'operator quote only; cancel without purchase', **metadata}}})
                state = task['status']['state']
                step = {'buyer_metadata': label, 'task_id': task['id'], 'state': state,
                        'ask': task_metadata(task).get('ask'), 'receipt': False}
                report['steps'].append(step)
                assert not task.get('artifacts'), 'Quote probe must not receive a receipt'
                if label == 'cheapest-in-town':
                    assert 'REJECTED' in state
                else:
                    try:
                        assert 'INPUT_REQUIRED' in state, state
                        assert step['ask'] == 100
                    finally:
                        if 'INPUT_REQUIRED' in state:
                            canceled = a2a.rpc('CancelTask', {'id': task['id']})
                            step['canceled_state'] = canceled['status']['state']
                            assert 'CANCELED' in step['canceled_state']
        finally:
            a2a.close()

        # The board's cached card can lag behind the successful join briefly.
        for attempt in range(6):
            after = client.get(MARKET + '/api/board').raise_for_status().json()
            row = next(row for row in after['shops'] if row['name'] == 'myeonggi')
            if 'Latest base quotes:' in row['card']['description']:
                break
            save(f'board-cache-pending-{attempt}.json', after)
            time.sleep(2)
        assert 'Latest base quotes:' in row['card']['description']
        registry_after = client.get(MARKET + '/api/shops').raise_for_status().json()
        save('deployment-board-after.json', after)
        save('deployment-shops-after.json', registry_after)
        assert identities(before) == identities(after)
        assert key_hashes() == old_keys
        shop = next(row for row in registry_after['shops'] if row['name'] == 'myeonggi')
        assert shop['pubkey'] == old_shop['pubkey'] and shop['url'] == PUBLIC
        for role in ('shops', 'buyers'):
            row = next(row for row in after[role] if row['name'] == 'myeonggi')
            assert row['alive'] and row['owner']['login'] == 'SUNGMYEONGGI'
        for pid in (KIT_PID, 3535109, int((SUB / '.market/shop-service/pid').read_text())):
            assert Path(f'/proc/{pid}/cmdline').read_bytes()
        report.update(passed=True, no_new_profiles=True, identities=identities(after),
                      keys_unchanged=True, public_url_unchanged=True,
                      kit_and_tunnel_restarted=False, service=control.get('http://localhost/').json())
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc)[:500])
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save('deployment-growth.json', report)
        print(json.dumps({key: value for key, value in report.items() if key != 'service'}, ensure_ascii=False, indent=2))
