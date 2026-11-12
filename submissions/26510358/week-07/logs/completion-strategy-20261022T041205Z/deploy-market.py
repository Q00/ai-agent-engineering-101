"""Reload the approved completion strategy and profile and refresh only the existing shop registration.

No purchase, order, review, quote or additional profile is created by this helper.
Buyer code is loaded by the existing kit for each newly issued order.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import httpx

HERE = Path(__file__).resolve().parent
SUB = HERE.parents[1]
REPO = SUB.parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import market_kit

PUBLIC = 'https://wisconsin-dylan-nhs-myspace.trycloudflare.com'
EXPECTED = ['Catalog search and price negotiation', 'Classic Reading Lamp', 'Classic Dining Chair', 'Classic Oak Bookshelf']
DESCRIPTION = 'Ssadassa Shop: lamp 100, chair 180, bookshelf 260 credits each. Clear quotes, prompt acceptance and Ed25519-signed receipts matching the agreed item and price. Request a current quote, then offer that total on the same task. (Catalog search and price negotiation)'
KIT_PID = 3566788
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'public_url': PUBLIC,
          'orders_or_purchases_created': False, 'review_style': 'concise_transaction_facts_v1'}


def save(filename, data):
    (HERE / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def owned(board):
    return {role: sorted((row['name'], row['joined']) for row in board[role]
                         if (row.get('owner') or {}).get('login') == 'SUNGMYEONGGI')
            for role in ('shops', 'buyers')}


def key_hashes():
    return {name: hashlib.sha256((SUB / name).read_bytes()).hexdigest()
            for name in ('shop_key.pem', 'buyer_key.pem')}


with httpx.Client(timeout=20, trust_env=False) as client, httpx.Client(
        transport=httpx.HTTPTransport(uds=str(SUB / '.market/shop-service/control.sock')), timeout=30) as control:
    try:
        before = client.get('https://market.ourolabs.ai/api/board').raise_for_status().json()
        shops_before = client.get('https://market.ourolabs.ai/api/shops').raise_for_status().json()
        save('deploy-board-before.json', before)
        save('deploy-shops-before.json', shops_before)
        old_shop = next(row for row in shops_before['shops'] if row['name'] == 'myeonggi')
        hashes = key_hashes()
        old = control.get('http://localhost/').raise_for_status().json()
        assert old['public_url'] == old_shop['url'] == PUBLIC
        assert b'market_kit.py' in Path(f'/proc/{KIT_PID}/cmdline').read_bytes()
        new = control.post('http://localhost/', json={'action': 'reload'}).raise_for_status().json()
        assert new['active_generation'] > old['active_generation'] and new['public_url'] == PUBLIC
        save('seller-reloaded.json', new)
        card = client.get(PUBLIC + '/.well-known/agent-card.json').raise_for_status().json()
        save('card-deployed.json', card)
        assert card['description'] == DESCRIPTION
        assert len(card['description']) <= 300
        assert not re.search('[가-힣]', json.dumps(card, ensure_ascii=False))
        assert [skill['name'] for skill in card['skills']] == EXPECTED
        assert all(row['url'].startswith(PUBLIC + '/') for row in card['supportedInterfaces'])

        # Reuse the existing claim in memory; never print or save it or join tokens.
        env = dict(row.split(b'=', 1) for row in Path(f'/proc/{KIT_PID}/environ').read_bytes().split(b'\0') if b'=' in row)
        assert (SUB / 'shop_key.pem').exists()
        key = market_kit.load_key(SUB / 'shop_key.pem')
        assert market_kit.pub_b64(key) == old_shop['pubkey']
        market = market_kit.Market('https://market.ourolabs.ai', env[b'MARKET_OWNER'].decode())
        try:
            joined = market.join('shop', 'myeonggi', key, PUBLIC)
            assert joined['name'] == 'myeonggi'
        finally:
            market.c.close()
        del env, joined, market

        for attempt in range(6):
            board = client.get('https://market.ourolabs.ai/api/board').raise_for_status().json()
            own_shop = next(row for row in board['shops'] if row['name'] == 'myeonggi')
            if own_shop['card']['description'] == card['description']:
                break
            save(f'board-cache-pending-{attempt}.json', board)
            time.sleep(2)
        save('deploy-board-after.json', board)
        shops_after = client.get('https://market.ourolabs.ai/api/shops').raise_for_status().json()
        save('deploy-shops-after.json', shops_after)
        assert own_shop['card']['description'] == card['description']
        assert [skill['name'] for skill in own_shop['card']['skills']] == EXPECTED
        assert owned(before) == owned(board)
        assert key_hashes() == hashes
        fresh_shop = next(row for row in shops_after['shops'] if row['name'] == 'myeonggi')
        assert fresh_shop['pubkey'] == old_shop['pubkey'] and fresh_shop['url'] == PUBLIC
        for role in ('shops', 'buyers'):
            row = next(row for row in board[role] if row['name'] == 'myeonggi')
            assert row['alive'] and row['owner']['login'] == 'SUNGMYEONGGI'
        for pid in (KIT_PID, 3535109, int((SUB / '.market/shop-service/pid').read_text())):
            assert Path(f'/proc/{pid}/cmdline').read_bytes()
        report.update(passed=True, public_description=card['description'],
                      source_commit=__import__('subprocess').check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
                      skill_names=EXPECTED, english_only=True, no_new_profiles=True,
                      keys_and_public_url_unchanged=True, kit_and_tunnel_restarted=False,
                      active_generation=new['active_generation'], profiles=owned(board))
    except BaseException as exc:
        report.update(passed=False, error=type(exc).__name__ + ': ' + str(exc)[:400])
        raise
    finally:
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        save('deployment-profile.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
