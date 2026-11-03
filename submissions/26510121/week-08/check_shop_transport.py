"""Probe the real seller/proxy through a temporary public tunnel, without joining.

Only the two discovery GET routes are exposed. Keys and state are synthetic;
this cannot negotiate, buy inventory, settle, or publish market messages.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import time

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.responses import JSONResponse

from r3.adapter import load_kit
from r3.state import ROOT, Journal
from r3.transport import open_tunnel
from seller_r3 import Seller, build, NAME


def check(provider='pinggy', protocol='http2'):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    log = ROOT / 'logs' / f'shop-transport-{stamp}.txt'
    report_path = log.with_suffix('.json')
    report = dict(checked_utc=stamp, provider=provider, protocol=protocol if provider == 'cloudflare' else 'pinggy-sdk',
                  public_discovery_verified=False, market_joined=False,
                  public_settlement_verified=False, log=str(log))
    kit = load_kit()
    proc = proxy = server = None
    seller_thread = None

    class ReadOnlyProxy(kit.Proxy):
        async def handle(self, req):
            if req.method != 'GET' or req.path_params['path'] not in (
                    '.well-known/agent-card.json', '.well-known/acp.json'):
                return JSONResponse({'error': 'discovery-only transport probe'}, status_code=403)
            return await super().handle(req)

    class NoMarket:
        def call(self, *args, **kwargs):
            raise RuntimeError('Market access is disabled in the transport probe')

    with tempfile.TemporaryDirectory(prefix='r3-shop-transport-') as scratch:
        try:
            sport, pport = kit.free_port(), kit.free_port()
            server = uvicorn.Server(uvicorn.Config(
                build(sport, Seller(Journal(Path(scratch)))), host='127.0.0.1',
                port=sport, log_level='error'))
            seller_thread = threading.Thread(target=server.run, daemon=True)
            seller_thread.start()
            if not kit.wait_card(f'http://127.0.0.1:{sport}', 10):
                raise RuntimeError('Local seller discovery failed')
            proxy = ReadOnlyProxy(pport)
            proxy.target(f'http://127.0.0.1:{sport}')
            proxy.acp = kit.AcpShop(Ed25519PrivateKey.generate(), NoMarket())
            proc, public = open_tunnel(kit, pport, provider, log, protocol)
            proxy.public = public
            report['temporary_public_url'] = public
            print(json.dumps({'checking_public_url': public}), flush=True)
            # Match the kit's discovery allowance: a fresh DNS record may lag
            # the quick-tunnel URL printed by cloudflared.
            deadline = time.monotonic() + 120
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                while time.monotonic() < deadline and proc.poll() is None:
                    try:
                        card = client.get(public + '/.well-known/agent-card.json')
                        acp = client.get(public + '/.well-known/acp.json')
                        report['last_http_status'] = dict(agent_card=card.status_code, acp=acp.status_code)
                        if card.status_code == acp.status_code == 200:
                            c, a = card.json(), acp.json()
                            interfaces = c.get('supportedInterfaces') or []
                            report['public_discovery_verified'] = bool(
                                c.get('name') == NAME and interfaces and
                                all(i['url'].startswith(public + '/a2a/') for i in interfaces) and
                                a.get('protocol') == 'acp' and a.get('endpoint') == public + '/acp')
                            if report['public_discovery_verified']:
                                break
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        report['last_probe_error'] = f'{type(exc).__name__}: {exc}'
                    time.sleep(2)
            if not report['public_discovery_verified']:
                report['error'] = 'Public discovery did not pass before the deadline'
        except Exception as exc:
            report['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if proc is not None:
                kit.stop(proc)
            if proxy is not None:
                proxy.server.should_exit = True
            if server is not None:
                server.should_exit = True
            if seller_thread:
                seller_thread.join(timeout=8)
            report['temporary_tunnel_stopped'] = proc is None or proc.poll() is not None
            with report_path.open('x', encoding='utf-8') as out:
                json.dump(report, out, ensure_ascii=False, indent=2)
    print(json.dumps(dict(report, report=str(report_path)), ensure_ascii=False, indent=2))
    return report['public_discovery_verified']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=['pinggy', 'cloudflare'], default='pinggy')
    parser.add_argument('--protocol', choices=['http2', 'quic'])
    args = parser.parse_args()
    raise SystemExit(0 if check(args.provider, args.protocol or 'http2') else 1)
