"""Required Challenge 1: test a descriptive buyer against the 2,000-item catalog.

Writes an unedited console capture and seller output into logs/challenge-1/.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from a2a_client import asking_price, jsonrpc_url, read_card, send_message, task_state
from catalog_search import load_catalog

HERE = Path(__file__).resolve().parent
OUT = HERE / 'logs' / 'challenge-1'
QUERIES = [
    'something wooden to keep about 40 books, not taller than my desk',
    'an adjustable task light for reading beside a desk with bamboo material',
    'a comfy compact seat for office work made from recycled material',
]


def port_available() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog()
    if len(catalog) < 1000:
        raise RuntimeError('Challenge 1 requires a committed catalog of thousands of items')
    port = port_available()
    base = f'http://127.0.0.1:{port}'
    with (OUT / 'seller-console.log').open('w', encoding='utf-8') as sf:
        proc = subprocess.Popen([sys.executable, 'seller.py', '--port', str(port)], cwd=HERE,
                                stdout=sf, stderr=subprocess.STDOUT)
        try:
            card = None
            for _ in range(45):
                try:
                    card = read_card(base)
                    break
                except Exception:
                    time.sleep(1)
            if card is None:
                raise RuntimeError('seller Agent Card not ready')
            url = jsonrpc_url(card)
            with (OUT / 'challenge-1.log').open('w', encoding='utf-8') as out:
                def record(s):
                    print(s)
                    out.write(s + '\n')
                    out.flush()
                record(f'catalog_size={len(catalog)} agent_card={card["name"]} protocol=A2A/1.0')
                for q in QUERIES:
                    record(f'QUERY: {q}')
                    task = send_message(url, q)
                    ask = asking_price(task)
                    state = task_state(task)
                    record(f'RESPONSE: taskId={task.get("id")} state={state} asking_price={ask}')
                    if state != 'TASK_STATE_INPUT_REQUIRED' or ask is None:
                        record('FAIL: no numeric counteroffer for the descriptive query')
                    else:
                        record('SUCCESS: catalog-based seller returned a numeric price')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

if __name__ == '__main__':
    run()
