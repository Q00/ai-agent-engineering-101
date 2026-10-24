"""Launch 20 model-driven shops and the multi-shop buyer; save actual run logs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parent


def read_events(path):
    events = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict): events.append(event)
        except ValueError:
            pass
    return events


def main():
    if not os.getenv('GROQ_API_KEY'): sys.exit('Set GROQ_API_KEY in this terminal first.')
    if not (ROOT / 'seller_catalog.py').exists(): sys.exit('Put these files beside seller_catalog.py in week-06.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    dest = ROOT / 'logs' / 'challenge-2' / stamp; dest.mkdir(parents=True)
    shops = json.loads((ROOT / 'shops.json').read_text(encoding='utf-8'))
    shopping = json.loads((ROOT / 'shopping_list.json').read_text(encoding='utf-8'))
    if len(shops) != 20: sys.exit('This demonstration expects 20 shops.')
    reservations = []; config = []
    for shop in shops:
        sock = socket.socket();sock.bind(('127.0.0.1', 0));reservations.append(sock)
        config.append({**shop, 'url': f'http://127.0.0.1:{sock.getsockname()[1]}',
            'catalog_path': str((ROOT / shop['catalog']).resolve())})
    directory = [{'shop_id': s['shop_id'], 'url': s['url']} for s in config]
    for name, data in [('server_config.json', config), ('directory.json', directory), ('shopping_list.json', shopping)]:
        (dest/name).write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
    for sock in reservations:sock.close()
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
    server_log = (dest/'sellers.log').open('w', encoding='utf-8')
    buyer_log = (dest/'buyer.log').open('w', encoding='utf-8')
    seller_proc = buyer_proc = None
    try:
        seller_proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'serve_shops.py'),
            '--config', str(dest/'server_config.json')], cwd=ROOT, env=env,
            stdout=server_log, stderr=subprocess.STDOUT)
        pending = {s['url'] for s in config}; deadline = time.monotonic()+90
        with httpx.Client(headers={'A2A-Version':'1.0'}, timeout=2) as http:
            while pending and time.monotonic()<deadline:
                if seller_proc.poll() is not None: raise RuntimeError('Shop servers stopped; inspect sellers.log')
                for url in list(pending):
                    try:
                        if http.get(url+'/.well-known/agent-card.json').status_code==200: pending.remove(url)
                    except httpx.RequestError: pass
                if pending: time.sleep(1)
        if pending: raise RuntimeError(f'{len(pending)} shops failed to start')
        print('20/20 shops ready. Buyer is reading cards, allocating budget and negotiating.', flush=True)
        print(f'Live detail is saved to: {dest}', flush=True)
        buyer_proc = subprocess.Popen([sys.executable, '-u', str(ROOT/'buyer_multi.py'),
            '--directory', str(dest/'directory.json'), '--shopping', str(dest/'shopping_list.json'),
            '--output', str(dest/'buyer-report.json')], cwd=ROOT, env=env,
            stdout=buyer_log, stderr=subprocess.STDOUT)
        # Challenge 2 adds routing calls; the basic buyer's 300s limit applies to
        # buyer.py in the official trade test, which is not changed here.
        deadline = time.monotonic()+1200;last_progress=time.monotonic()
        while buyer_proc.poll() is None and time.monotonic()<deadline:
            time.sleep(1)
            if time.monotonic()-last_progress>=20:
                events=read_events(dest/'buyer.log')
                print(f"Working: {sum(e.get('event')=='card_read' for e in events)} cards read; "
                      f"{sum(e.get('event')=='purchased' for e in events)}/{len(shopping['items'])} purchases. "
                      'Model requests may pause for rate limits.', flush=True)
                last_progress=time.monotonic()
        if buyer_proc.poll() is None:
            buyer_proc.terminate();buyer_proc.wait(timeout=10)
            raise TimeoutError('Challenge buyer exceeded 1200 seconds')
        buyer_log.flush();server_log.flush()
        report_path=dest/'buyer-report.json'
        if not report_path.exists(): raise RuntimeError('Buyer stopped before final report; inspect buyer.log')
        report=json.loads(report_path.read_text(encoding='utf-8'))
        sold=[e for e in read_events(dest/'sellers.log') if e.get('event')=='sold']
        expected={i['item_id'] for i in shopping['items']}
        purchased=report.get('purchases',[])
        ids=[p['item_id'] for p in purchased]
        receipts_ok=True
        products={}
        for shop in config:
            for product in json.loads(Path(shop['catalog_path']).read_text(encoding='utf-8')):
                products[product['sku']]=product
        for sale in sold:
            p=products.get(sale.get('sku'))
            if not p or type(sale.get('price')) not in (int,float) or sale['price']<p['reserve']:
                receipts_ok=False
        # Check seller-side sales too: a dictionary cannot hide duplicate buys.
        sold_skus=[s.get('sku') for s in sold]
        purchased_skus=[p['receipt'].get('sku') for p in purchased]
        no_duplicates=len(ids)==len(set(ids)) and len(sold)==len(expected) and sorted(sold_skus)==sorted(purchased_skus)
        total=sum(p['price'] for p in purchased)
        events=read_events(dest/'buyer.log');active=set();peak=0
        for e in events:
            if e.get('event')=='negotiation_started':active.add(e['item_id']);peak=max(peak,len(active))
            if e.get('event') in ('purchased','walked_away'):active.discard(e['item_id'])
        checks={'all_items_bought':set(ids)==expected, 'within_total_budget':total<=shopping['total_budget'],
            'no_duplicate_purchases':no_duplicates,'seller_reserves_held':receipts_ok,
            'twenty_cards_read':report.get('shop_count')==20,'overlapping_negotiations':peak>=2}
        result={'passed':report.get('passed') is True and all(checks.values()), 'checks':checks,
            'total_spent':total,'total_budget':shopping['total_budget'],'peak_open_negotiations':peak,
            'purchase_count':len(purchased),'seller_sale_count':len(sold),'buyer_report':'buyer-report.json'}
        (dest/'report.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        for name, ok in checks.items():print(f"{'PASS' if ok else 'FAIL'} {name}",flush=True)
        print(f"{len(purchased)}/{len(expected)} items; total {total}/{shopping['total_budget']} -> "
              f"{'PASS' if result['passed'] else 'FAIL'}",flush=True)
        print(f'Logs: {dest}',flush=True)
        return 0 if result['passed'] else 1
    except Exception as error:
        (dest/'runner-error.json').write_text(json.dumps({'error_type':type(error).__name__,
            'message':str(error)[:300]},indent=2)+'\n',encoding='utf-8')
        print(f'Run failed: {type(error).__name__}. Logs: {dest}',flush=True)
        return 1
    finally:
        for proc in [buyer_proc,seller_proc]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        buyer_log.close();server_log.close()


if __name__ == '__main__':
    raise SystemExit(main())
