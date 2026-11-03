"""Current v4 readiness, distinct from the preserved legacy readiness.py."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
from urllib.request import Request, urlopen
from r3.state import ROOT, policy
from r3.model import api_key


def inspect():
    checks = {name: (ROOT / name).is_file() for name in
              ['agent.json', 'seller_r3.py', 'market_buyer.py', 'restock.py', 'review.py',
               'run_market.py', 'shop_key.pem', 'buyer_key.pem', '.market/owner.env']}
    checks['api_credential_present'] = bool(api_key())
    checks['a2a_dependency'] = importlib.util.find_spec('a2a') is not None
    checks['pinggy_dependency'] = importlib.util.find_spec('pinggy') is not None
    checks['tunnel_binary'] = (ROOT / '.market/bin/cloudflared.exe').is_file() or bool(shutil.which('cloudflared'))
    report = {'checked_utc': datetime.now(timezone.utc).isoformat(), 'checks': checks,
              'locally_prepared_for_both_roles': all(checks.values()),
              'actor_model': policy()['actor_model'], 'critic_model': policy()['critic_model'],
              'public_registration_verified': False, 'public_r3_settlement_verified': False}
    try:
        with urlopen(Request('https://market.ourolabs.ai/api/board', headers={'User-Agent': 'R3-readiness/1.0'}), timeout=15) as r:
            board = json.loads(r.read())
        report['market_current_round'] = board.get('current')
    except Exception as exc:
        report['public_read_error'] = type(exc).__name__
    return report


if __name__ == '__main__':
    report = inspect()
    p = ROOT / 'logs' / ('runtime-readiness-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    with p.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(dict(report, log=str(p)), ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['locally_prepared_for_both_roles'] else 1)
