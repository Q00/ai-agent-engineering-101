#!/usr/bin/env python3
"""Safely inspect a restock_r3 plan. No API calls; no real purchases.

At cold start use --simulate-round3: only TEMPORARY copies are modified.
When the market is actually Round 3, omit that flag.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import tempfile

from restock_r3 import make_plan, read_json, settled_signals


def run(state: Path, simulate=False):
    if simulate:
        with tempfile.TemporaryDirectory(prefix='week08-preview-') as tmp:
            temp = Path(tmp)
            for name in ('inventory.json', 'catalog.json', 'demand.json', 'payments.jsonl', 'forum.json'):
                if (state / name).exists():
                    shutil.copy2(state / name, temp / name)
            inv = read_json(temp / 'inventory.json', {})
            inv.update({'round': 3, 'balance': 5_000_000, 'units': []})
            (temp / 'inventory.json').write_text(json.dumps(inv), encoding='utf-8')
            # No historical logs inside the temporary preview; on-market run reads real logs.
            show(temp, real=False)
    else:
        show(state, real=True)


def show(state, real):
    plan = make_plan(state)
    catalog = read_json(state / 'catalog.json', {})
    inv = read_json(state / 'inventory.json', {})
    categories, estimated_cost = Counter(), 0.0
    print('\n', 'LIVE-STATE READ-ONLY' if real else 'SIMULATED ROUND 3 (NO PURCHASES)')
    for p in plan['buy']:
        meta = catalog.get(p['sku'], {})
        cat = meta.get('category', '')
        categories[cat.split('>')[0]] += p['qty']
        cost = .75 * float(meta.get('list_price') or 0) * p['qty']
        estimated_cost += cost
        print(f"{p['sku']} | {str(meta.get('name',''))[:45]} | list={meta.get('list_price')} | max-cost={round(cost)}")
    print('Proposed lines:', len(plan['buy']))
    print('Top-level categories:', dict(categories))
    print('Maximum estimated supplier spend:', round(estimated_cost))
    print('Balance after upper-bound spend:', round(float(inv.get('balance') or 0) - estimated_cost))
    print('No market API requests were made.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', type=Path, required=True)
    ap.add_argument('--simulate-round3', action='store_true')
    args = ap.parse_args()
    run(args.state, simulate=args.simulate_round3)
