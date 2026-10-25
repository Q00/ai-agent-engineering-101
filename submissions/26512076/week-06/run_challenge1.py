"""Run description-based purchases; save genuine logs under logs/challenge-1/.

This runner is a fixed buyer. Product selection and seller negotiation use Groq.
Each run uses a new timestamped directory, retaining earlier attempts.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
import httpx

ROOT = Path(__file__).resolve().parent
CASES = [
    {'name': 'wooden_book_storage',
     'query': 'I need something wooden to keep about 40 books, no taller than 75 cm, to fit beside my desk.',
     'requirements': {'category': 'bookshelf', 'material': 'wood', 'capacity_books': 40, 'max_height_cm': 75}},
    {'name': 'dimmable_desk_light',
     'query': 'I need a metal light for my desk with adjustable brightness, dimmable, no taller than 40 cm.',
     'requirements': {'category': 'lamp', 'material': 'metal', 'dimmable': True, 'max_height_cm': 40}},
    {'name': 'ergonomic_seating',
     'query': 'I need something ergonomic to sit on in my office, with adjustable seat height and back support, no taller than 90 cm.',
     'requirements': {'category': 'chair', 'ergonomic': True, 'adjustable': True, 'max_height_cm': 90}},
]


def matches(product, requirements):
    return all(product.get('height_cm', float('inf')) <= v if k == 'max_height_cm'
               else product.get(k) == v for k, v in requirements.items())


def run():
    if not os.getenv('GROQ_API_KEY'):
        sys.exit('Set GROQ_API_KEY in this terminal first.')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    dest = ROOT / 'logs' / 'challenge-1' / timestamp
    dest.mkdir(parents=True)
    log_file = (dest / 'buyer.log').open('w', encoding='utf-8')
    def log(event, **fields):
        line = json.dumps({'event': event, **fields}, ensure_ascii=True)
        print(line, flush=True)
        log_file.write(line + '\n'); log_file.flush()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    server_log = (dest / 'seller.log').open('w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-u', str(ROOT / 'seller_catalog.py'), '--port', str(port)],
        cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT)
    results = []
    products = {p['sku']: p for p in json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))}
    try:
        with httpx.Client(headers={'A2A-Version': '1.0'}, timeout=150) as client:
            for _ in range(60):
                if proc.poll() is not None:
                    raise RuntimeError('Catalog seller stopped; inspect seller.log')
                try:
                    r = client.get(url + '/.well-known/agent-card.json', timeout=2)
                    if r.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError('Catalog seller did not start within 60 seconds')
            def send(text, tid=None):
                msg = {'role': 'ROLE_USER', 'messageId': str(uuid.uuid4()), 'parts': [{'text': text}]}
                if tid: msg['taskId'] = tid
                log('send', text=text, taskId=tid)
                r = client.post(url + '/', json={'jsonrpc': '2.0', 'id': str(uuid.uuid4()),
                    'method': 'SendMessage', 'params': {'message': msg}})
                r.raise_for_status(); body = r.json()
                if 'error' in body: raise RuntimeError(f"A2A error: {body['error'].get('code')}")
                result = body['result']; task = result.get('task', result)
                log('receive', task=task)
                return task
            for case in CASES:
                asks = []; selected_sku = None
                log('case', **case)
                try:
                    task = send(case['query']); tid = task['id']
                    for turn in range(8):
                        state = task['status']['state']
                        if state != 'TASK_STATE_INPUT_REQUIRED': break
                        message = task['status'].get('message', {})
                        meta = message.get('metadata', {})
                        sku, ask = meta.get('sku'), meta.get('ask')
                        if sku not in products or type(ask) not in (float, int):
                            raise ValueError('Missing SKU/ask metadata')
                        product = products[sku]
                        if selected_sku is not None and selected_sku != sku:
                            raise ValueError('Seller switched products')
                        selected_sku = sku
                        if not matches(product, case['requirements']):
                            raise ValueError('Selected product does not meet the buyer constraints')
                        if ask < product['reserve']:
                            raise ValueError('Ask below selected product reserve')
                        asks.append(ask)
                        # A successful purchase is checked independently of low-offer rejection.
                        offer = ask
                        task = send(str(offer), tid)
                    receipt = next((part['data'] for a in task.get('artifacts', [])
                        for part in a.get('parts', []) if isinstance(part.get('data'), dict)
                        and 'price' in part['data']), None)
                    if task['status']['state'] != 'TASK_STATE_COMPLETED' or not receipt:
                        raise ValueError('Purchase did not complete')
                    product = products.get(receipt.get('sku'))
                    if not product or receipt['sku'] != selected_sku or not matches(product, case['requirements']):
                        raise ValueError('Wrong product in receipt')
                    price = receipt.get('price')
                    if type(price) not in (int, float) or price < product['reserve']:
                        raise ValueError('Receipt below reserve or missing price')
                    purchase = {'sku': product['sku'], 'price': price, 'reserve': product['reserve'], 'asks': asks}
                    # New task: independently check how a below-reserve offer is handled.
                    probe = send(case['query']); probe_tid = probe['id']
                    if probe['status']['state'] != 'TASK_STATE_INPUT_REQUIRED':
                        raise ValueError('Reserve probe did not open with a quote')
                    probe_sku = None
                    probe_asks = []
                    for probe_turn in range(3):
                        probe_state = probe['status']['state']
                        if probe_state == 'TASK_STATE_REJECTED':
                            break  # Rejection of a below-reserve offer is valid.
                        if probe_state != 'TASK_STATE_INPUT_REQUIRED':
                            raise ValueError('Reserve probe unexpectedly completed or failed')
                        meta = probe['status'].get('message', {}).get('metadata', {})
                        q = products.get(meta.get('sku')); ask = meta.get('ask')
                        if not q or not matches(q, case['requirements']):
                            raise ValueError('Reserve probe selected an unsuitable product')
                        if probe_sku is not None and probe_sku != q['sku']:
                            raise ValueError('Reserve probe switched products')
                        probe_sku = q['sku']
                        if type(ask) not in (int, float) or ask < q['reserve']:
                            raise ValueError('Reserve probe quote below reserve')
                        probe_asks.append(ask)
                        if probe_turn < 2:
                            probe = send(str(max(1, q['reserve'] // 2)), probe_tid)
                    log('reserve_probe_result', case=case['name'], state=probe['status']['state'], asks=probe_asks)
                    result = {'name': case['name'], 'pass': True, 'query': case['query'],
                        **purchase, 'reserve_probe_state': probe['status']['state'], 'reserve_probe_asks': probe_asks}
                except Exception as error:
                    result = {'name': case['name'], 'pass': False, 'error': str(error), 'asks': asks}
                results.append(result); log('case_result', **result)
        passed = len(results) == len(CASES) and all(r['pass'] for r in results)
        report = {'catalog_count': len(products), 'model': os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b'),
                  'passed': passed, 'results': results}
        (dest / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f"{sum(r['pass'] for r in results)}/{len(CASES)} cases passed -> {'PASS' if passed else 'FAIL'}", flush=True)
        print(f'Logs: {dest}', flush=True)
        return 0 if passed else 1
    finally:
        proc.terminate()
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        server_log.close(); log_file.close()


if __name__ == '__main__':
    raise SystemExit(run())
