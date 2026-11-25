"""Real model, copied public inventory, synthetic supply window; no supply API."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from harness import decide_json
import inventory_portfolio
from market_memory import events


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--copy-policy-state', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    runtime = ROOT/'state/seller-runtime/.market'
    raw = (runtime/'catalog.json').read_bytes()
    products = json.loads(raw)
    inv = json.loads((runtime/'inventory.json').read_text())
    history = events(path=ROOT/'logs/shop-events.jsonl')
    at = time.time()
    inv['restock'] = {'open': True, 'opens_at': at-10, 'closes_at': at+180}
    (output/'fixture-inventory.json').write_text(json.dumps(inv, ensure_ascii=False, indent=2))
    policy = ROOT/'state/seller-runtime/state/inventory-portfolio.json'
    if args.copy_policy_state:
        (output/'state').mkdir()
        (output/'state/inventory-portfolio.json').write_bytes(policy.read_bytes())
        (output/'fixture-policy-state.json').write_bytes(policy.read_bytes())
    os.chdir(output)
    result = inventory_portfolio.run(inv, products, history, at, model=decide_json,
                                    refresh=lambda: (inv, time.time(), True), rng=random.Random(1))
    summary = {'experiment': 'real model, copied live inventory, synthetic supply window',
               'catalog_sha256': hashlib.sha256(raw).hexdigest(), 'result': result,
               'copied_live_policy_state': args.copy_policy_state,
               'selected': [{**b, 'name': products[b['sku']]['name'],
                             'category': products[b['sku']]['category'], 'kind': products[b['sku']]['kind']}
                            for b in (result or {}).get('buy', [])],
               'real_supply_requests': 0, 'deployment': False}
    (output/'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
