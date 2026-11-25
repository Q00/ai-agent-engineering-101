"""Same-address recovery when the primary supervisor is stale.

No strategy, key, tunnel, trade or primary-health changes. Exits when the pinned
runtime changes; suppresses repair during global outages and unverified probes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from r3.lifecycle import lease, market_active
from r3.state import ROOT, atomic_json, read_json


def decide(probe, state, now):
    state = dict(state)
    reason = 'unverified'
    if not probe.get('runtime_matches'):
        return 'stop', dict(state, misses=0), 'runtime_changed'
    recent_primary=probe.get('primary_age',0)<120
    if recent_primary and not probe.get('cooperative_recovery'):
        return 'wait', dict(state, misses=0), 'primary_supervisor_recent'
    if not probe.get('verified'):
        return 'wait', dict(state, misses=0), reason
    if not probe.get('identity_matches'):
        return 'wait', dict(state, misses=0), 'identity_mismatch'
    if not probe.get('public_ready'):
        return 'wait', dict(state, misses=0), 'public_endpoint_unhealthy'
    if not probe.get('registry_count') or not probe.get('active_peers'):
        return 'wait', dict(state, misses=0), 'market_outage_or_no_active_peers'
    if probe.get('listed') and probe.get('shop_alive') is True:
        return 'healthy', dict(state, misses=0), 'public_registry_and_board_agree'
    if probe.get('shop_alive') not in (True, False):
        return 'wait', dict(state, misses=0), 'board_state_unknown'
    state['misses'] = state.get('misses', 0) + 1
    if state['misses'] < 2:
        return 'wait', state, 'confirm_again'
    if recent_primary and probe.get('unverified_seconds',0)<180:
        return 'wait',state,'allow_current_address_verification_grace'
    if now - state.get('last_attempt', 0) < 120:
        return 'wait', state, 'repair_cooldown'
    return ('request' if recent_primary else 'repair'), dict(state, last_attempt=now, misses=0), 'confirmed_own_discovery_failure'


def probe(url, pinned):
    def get(path):
        try:
            with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = client.get(path, headers={'Cache-Control': 'no-cache'})
                return response.status_code, response.json() if response.status_code == 200 else {}
        except Exception:
            return None, {}
    paths = [url + '/.well-known/agent-card.json', url + '/.well-known/acp.json',
             'https://market.ourolabs.ai/api/shops', 'https://market.ourolabs.ai/api/board']
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(get, paths))
    card, acp, registry, board = [r[1] for r in rows]
    own = [r for r in board.get('shops', []) if r.get('name') == 'seochanit']
    shops = registry.get('shops', [])
    health = read_json(ROOT / '.market/shop-health.json', {})
    primary = health.get('primary_heartbeat_at', health.get('checked_at', 0))
    grace=health.get('seconds_since_verification_or_grace',health.get('seconds_since_market_verified',0))
    actual=health.get('seconds_since_market_verified')
    unverified=min(actual,grace) if type(actual) in (int,float) and type(grace) in (int,float) else grace
    result = {
        'runtime_matches': read_json(ROOT / '.market/runtime.json', {}) == pinned and market_active(),
        'primary_age': time.time() - primary,
        'verified': all(r[0] == 200 for r in rows),
        'identity_matches': len(own) == 1 and (own[0].get('card') or {}).get('url', '').rstrip('/') == url,
        'public_ready': card.get('name') == 'Seochanit Shop' and acp.get('protocol') == 'acp',
        'registry_count': len(shops),
        'listed': any(r.get('name') == 'seochanit' and r.get('url', '').rstrip('/') == url for r in shops),
        'shop_alive': own[0].get('alive') if len(own) == 1 else None,
        'active_peers': sum(r.get('name') != 'seochanit' and r.get('alive') is True for r in board.get('shops', [])),
        'cooperative_recovery':read_json(ROOT/'policy.json',{}).get('cooperative_recovery_enabled',False),
        'unverified_seconds':unverified if type(unverified) in (int,float) else 0,
    }
    return result


def repair(pinned, url):
    if read_json(ROOT/'policy.json',{}).get('cooperative_recovery_enabled'):
        try:
            with lease(ROOT/'.market/discovery-mutation.lock'):
                return _repair(pinned,url)
        except RuntimeError as exc:
            if 'already holds' not in str(exc):raise
            return {'attempted':False,'reason':'primary_mutation_in_progress'}
    return _repair(pinned,url)


def _repair(pinned, url):
    # Recheck immediately before invoking the existing identity-bound helper.
    if read_json(ROOT / '.market/runtime.json', {}) != pinned or not market_active():
        return {'attempted': False, 'reason': 'runtime_changed'}
    if read_json(ROOT / '.market/shop-health.json', {}).get('url', '').rstrip('/') != url:
        return {'attempted': False, 'reason': 'address_changed'}
    env = {k: v for k, v in os.environ.items() if k not in {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY'}}
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'restore_shop_registration.py')],
                                cwd=ROOT, env=env, timeout=55, capture_output=True)
        # Never forward credentials, arbitrary server errors or raw subprocess text.
        return {'attempted': True, 'helper_exit_code': result.returncode,
                'registration_acceptance_is_not_recovery_proof': True}
    except subprocess.TimeoutExpired:
        return {'attempted': True, 'error_type': 'TimeoutExpired', 'join_outcome': 'unknown'}


def main():
    pinned = read_json(ROOT / '.market/runtime.json', {})
    url = read_json(ROOT / '.market/shop-health.json', {}).get('url', '').rstrip('/')
    parts = urlsplit(url)
    if not pinned.get('session') or not market_active() or parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise SystemExit('A live existing runtime and its public URL are required.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log_path = ROOT / 'logs' / ('shop-recovery-guard-' + stamp + '.jsonl')
    state = {'misses': 0, 'last_attempt': 0}
    with lease(ROOT / '.market/shop-recovery-guard.lock'), log_path.open('x', encoding='utf-8') as stream:
        print('SOURCE', log_path, flush=True)
        while True:
            now = time.time()
            try:
                # Follow only this pinned runtime's current address after a
                # primary-controlled tunnel rotation; never reuse an old URL.
                current=read_json(ROOT/'.market/shop-health.json',{}).get('url','').rstrip('/')
                parsed=urlsplit(current)
                if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password:
                    url=current
                evidence = probe(url, pinned)
                action, state, reason = decide(evidence, state, now)
                row = {'at': now, 'pid': os.getpid(), 'runtime_session': pinned['session'],
                       'url': url, 'action': action, 'reason': reason, 'probe': evidence, 'state': state}
                if action == 'repair':
                    row['repair'] = repair(pinned, url)
                elif action=='request':
                    from r3.recovery import request
                    row['request_id']=request(ROOT/'.market',pinned,url,evidence,now)
                if action == 'stop':
                    stream.write(json.dumps(row) + '\n'); stream.flush()
                    return
            except Exception as exc:
                state['misses'] = 0
                row = {'at': now, 'pid': os.getpid(), 'action': 'wait', 'error_type': type(exc).__name__}
            # Diagnostic failures must not terminate the sole fallback worker.
            try:
                stream.write(json.dumps(row) + '\n'); stream.flush()
                atomic_json(ROOT / '.market/shop-recovery-guard.json', row)
            except Exception as exc:
                print('diagnostic_write_failed', type(exc).__name__, flush=True)
            time.sleep(30)


if __name__ == '__main__':
    main()
