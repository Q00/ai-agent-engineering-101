"""Required Challenge 2: discover 20 live Agent Cards and buy 3 items.

The test creates 20 copies of the user's A2A seller with varied reserve
prices, runs multi_shopper.py, then shuts them all down. Captures are saved
unaltered under logs/challenge-2/. Resource note: 20 Python servers need RAM.
"""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from a2a_client import read_card

HERE = Path(__file__).resolve().parent
OUT = HERE / 'logs' / 'challenge-2'


def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shops', type=int, default=20)
    args = ap.parse_args()
    if args.shops < 3:
        raise SystemExit('--shops must be at least 3')
    OUT.mkdir(parents=True, exist_ok=True)
    directory = []
    handles = []
    try:
        for idx in range(args.shops):
            port = free_port()
            name = f'Furniture Shop {idx:02d}'
            delta = (idx % 20) - 5
            specialty = ('lamp', 'chair', 'bookshelf')[idx % 3]
            url = f'http://127.0.0.1:{port}'
            stream = (OUT / f'shop-{idx:02d}.log').open('w', encoding='utf-8')
            proc = subprocess.Popen(
                [sys.executable, 'seller.py', '--port', str(port), '--name', name,
                 '--reserve-delta', str(delta), '--specialty', specialty],
                cwd=HERE, stdout=stream, stderr=subprocess.STDOUT,
            )
            handles.append((proc, stream))
            directory.append({'id': f'shop-{idx:02d}', 'url': url})
            time.sleep(0.15)
        (OUT / 'directory.json').write_text(json.dumps(directory, indent=2) + '\n', encoding='utf-8')
        shopping_list = [
            {'item': 'lamp', 'budget': 130},
            {'item': 'chair', 'budget': 220},
            {'item': 'bookshelf', 'budget': 300},
        ]
        (OUT / 'shopping-list.json').write_text(json.dumps(shopping_list, indent=2) + '\n', encoding='utf-8')
        for entry in directory:
            ready = False
            for _ in range(40):
                try:
                    card = read_card(entry['url'])
                    print(f"[STARTED] {entry['id']} {card['name']}")
                    ready = True
                    break
                except Exception:
                    time.sleep(0.5)
            if not ready:
                raise RuntimeError(f"Seller {entry['id']} failed to publish an Agent Card")
        cmd = [sys.executable, 'multi_shopper.py',
               '--directory', str(OUT / 'directory.json'),
               '--shopping-list', str(OUT / 'shopping-list.json'),
               '--total-budget', '650', '--result', str(OUT / 'result.json')]
        with (OUT / 'challenge-2.log').open('w', encoding='utf-8') as stream:
            completed = subprocess.run(cmd, cwd=HERE, stdout=stream,
                                       stderr=subprocess.STDOUT, timeout=1800)
        print(f'[CHALLENGE 2] process exit={completed.returncode}; review logs/challenge-2/challenge-2.log')
        if completed.returncode:
            raise SystemExit(completed.returncode)
    finally:
        for proc, stream in handles:
            proc.terminate()
        for proc, stream in handles:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stream.close()


if __name__ == '__main__':
    main()
