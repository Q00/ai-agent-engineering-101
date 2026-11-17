#!/usr/bin/env python3
"""Controlled SYNTHETIC baseline-vs-adaptive buyer comparison.

Simulates fixed-ask sellers. Does NOT access market or claim live settlements.
"""
from __future__ import annotations
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buyer as legacy
import buyer_adaptive as adaptive
from test_buyer_adaptive import SimulatedSeller


def run(strategy, reserve, ask=100, budget=100):
    sim = SimulatedSeller(ask, reserve)
    client = MagicMock()
    client.__enter__.return_value = client
    mod = adaptive if strategy == 'adaptive' else legacy
    with tempfile.TemporaryDirectory() as td, \
         patch.object(adaptive, 'LOG_PATH', Path(td) / 'log.jsonl'), \
         patch.object(adaptive.httpx, 'Client', return_value=client), \
         patch.object(legacy, 'discover', return_value=({'name':'mock'}, 'http://invalid')), \
         patch.object(legacy, 'rpc', side_effect=sim.rpc), \
         patch.object(mod, 'decide', side_effect=lambda prompt, view, actions: {
             'action': 'offer',
             'price': view['recommended_offer'] if strategy == 'adaptive' else view['seller_ask'],
         }), \
         contextlib.redirect_stdout(io.StringIO()):
        result = mod.buy('http://example.invalid', 'GTEST', budget)
    return result, len(sim.offers)


def main():
    print('SYNTHETIC controlled fixed-ask seller test (NOT real market data)')
    print('reserve | baseline price/turns | adaptive price/turns | savings')
    savings = []
    for reserve in (75, 80, 85, 90, 95, 100, 102):
        a, at = run('adaptive', reserve)
        b, bt = run('baseline', reserve)
        if b['deal'] and a['deal']:
            saving = b['price'] - a['price']
            savings.append(saving)
            print(f'{reserve:7} | {b["price"]:5}/{bt:1} | {a["price"]:5}/{at:1} | {saving:3}')
        else:
            print(f'{reserve:7} | {"deal" if b["deal"] else "no deal":>7} | '
                  f'{"deal" if a["deal"] else "no deal":>7} | n/a')
    assert all(x >= 0 for x in savings), 'adaptive must not overpay baseline on fixture'
    assert any(x > 0 for x in savings), 'no gain in easy fixture scenarios'
    print(f'Completed matched cases: {len(savings)}; mean saved credits: {sum(savings)/len(savings):.2f}')
    print('No real purchases, payments or ledger records were generated.')


if __name__ == '__main__':
    main()
