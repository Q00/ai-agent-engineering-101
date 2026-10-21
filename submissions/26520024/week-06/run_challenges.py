"""Live required challenges only. Every invocation writes a new immutable run folder."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager, redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback

import httpx

from buyer import negotiate
from codex_backend import backend_info
from core import PLAN_PROMPT, decide, emit, last_price, validate_plan
from wire import HEADERS, Wire, task_text

ROOT = Path(__file__).resolve().parent


def save(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


@contextmanager
def server(folder, name, shop_id=None):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = 'http://127.0.0.1:{}/'.format(port)
    command = [sys.executable, '-u', str(ROOT / 'seller.py'), '--port', str(port)]
    if shop_id:
        command += ['--shop-id', shop_id]
    with (folder / (name + '.log')).open('x') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(headers=HEADERS, trust_env=False) as client:
                for _ in range(120):
                    if process.poll() is not None:
                        raise RuntimeError('Server startup failed: ' + name)
                    try:
                        response = client.get(url + '.well-known/agent-card.json', timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.25)
                else:
                    raise TimeoutError('Server readiness timeout')
            emit('server_ready', name=name, url=url, command=command)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            emit('server_stopped', name=name, exitcode=process.returncode)


def challenge_one(folder):
    products = {p['id']: p for p in json.loads((ROOT / 'catalog.json').read_text())}
    rows = []
    with server(folder, 'catalog-seller') as url:
        for case in json.loads((ROOT / 'challenge_cases.json').read_text()):
            wire = Wire(url, time.monotonic() + 300, dict(case_id=case['id']))
            row = dict(case_id=case['id'], query=case['query'], expected_id=case['expected_id'])
            try:
                wire.discover()
                task = wire.send(case['query'])
                if task['status']['state'] != 'TASK_STATE_INPUT_REQUIRED':
                    raise ValueError('No opening ask')
                ask = last_price(task_text(task))
                row.update(task_id=task['id'], opening_ask=ask)
                if ask is None:
                    raise ValueError('No integer ask')
                final = wire.send(str(ask), task['id'])
                receipts = [p['data'] for a in final.get('artifacts', []) for p in a.get('parts', []) if 'data' in p]
                row.update(state=final['status']['state'], receipts=receipts)
                expected = products[case['expected_id']]
                row['passed'] = (final['id'] == task['id'] and row['state'] == 'TASK_STATE_COMPLETED'
                                 and len(receipts) == 1 and receipts[0].get('item_id') == expected['id']
                                 and receipts[0].get('price') == ask and ask >= expected['reserve'])
            except Exception as exc:
                row.update(passed=False, error=str(exc))
                emit('challenge_error', case_id=case['id'], traceback=traceback.format_exc())
            finally:
                wire.close()
            rows.append(row)
            emit('challenge1_result', **row)
    return dict(challenge=1, passed=all(r['passed'] for r in rows), results=rows,
                buyer='Scripted accept-at-ask probe; seller selection and price are live model decisions')


def challenge_two(folder):
    shopping = json.loads((ROOT / 'shopping.json').read_text())
    shops = json.loads((ROOT / 'shops.json').read_text())
    rows = []
    with ExitStack() as stack:
        urls = {s['id']: stack.enter_context(server(folder, s['id'], s['id'])) for s in shops}
        cards, directory = {}, []
        for shop_id, url in urls.items():
            wire = Wire(url, time.monotonic() + 30, dict(shop_id=shop_id))
            try:
                card = wire.discover()
            finally:
                wire.close()
            cards[shop_id] = card
            directory.append(dict(shop_id=shop_id, name=card['name'][:100],
                                  skills=[dict(name=s['name'][:100], description=s['description'][:500],
                                               tags=s.get('tags', [])[:10]) for s in card['skills'][:3]]))
        save(folder / 'agent-cards.json', cards)
        for repeat in range(1, shopping['repeats'] + 1):
            run_id = 'shopping-{}'.format(repeat)
            row = dict(run_id=run_id, budget=shopping['budget'], directory_size=len(cards))
            try:
                plan = decide(PLAN_PROMPT, dict(items=shopping['items'], total_budget=shopping['budget'],
                                                shops=directory),
                              lambda p: validate_plan(p, shopping['items'], urls, shopping['budget']),
                              'buyer', time.monotonic() + 180, dict(run_id=run_id, stage='planning'))
                row['plan'] = plan
                save(folder / (run_id + '-plan.json'), plan)
                started = time.monotonic()
                outcomes = []
                with ThreadPoolExecutor(max_workers=len(shopping['items'])) as pool:
                    futures = {pool.submit(negotiate, urls[p['shop_id']], p['item'], p['budget'], run_id): p
                               for p in plan['purchases']}
                    for future in as_completed(futures):
                        purchase = futures[future]
                        try:
                            result = future.result()
                        except Exception as exc:
                            result = dict(outcome='error', error=str(exc))
                        outcomes.append(dict(purchase=purchase, result=result))
                total = sum(o['result'].get('receipt', {}).get('price', 0) for o in outcomes)
                bought = [o['result']['receipt']['item'] for o in outcomes if o['result']['outcome'] == 'bought']
                row.update(outcomes=outcomes, elapsed_seconds=time.monotonic() - started, total=total,
                           passed=sorted(bought) == sorted(shopping['items']) and total <= shopping['budget'])
            except Exception as exc:
                row.update(passed=False, error=str(exc))
                emit('challenge_error', run_id=run_id, traceback=traceback.format_exc())
            rows.append(row)
            emit('challenge2_result', **row)
    return dict(challenge=2, passed=all(r['passed'] for r in rows), results=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--challenge', type=int, choices=[1, 2], required=True)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = ROOT / 'logs' / ('challenge-' + str(args.challenge)) / stamp
    folder.mkdir(parents=True, exist_ok=False)
    print('Live logs: ' + str(folder), flush=True)
    with (folder / 'run.jsonl').open('x') as log, redirect_stdout(log):
        emit('backend', **backend_info())
        result = (challenge_one if args.challenge == 1 else challenge_two)(folder)
        save(folder / 'results.json', result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(0 if result['passed'] else 1)
