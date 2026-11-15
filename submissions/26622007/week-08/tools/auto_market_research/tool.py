"""Combined paid market search and matching buyer purchase history."""
import json
import os
import sys

import market_model
from tools.auto_market_scan.tool import search


def research(catalog, rows, trades, args):
    found = search(catalog, rows, args)
    limit = max(1, min(100, int(args.get('limit', 20))))
    out = {'catalog_count': found['matched_products'], 'catalog': found['catalog'],
           'paid_deal_count': found['matched_paid'], 'paid_deals': found['ledger']}
    if args.get('include_purchases'):
        # Match against the complete catalog, not its presentation-limited slice.
        products = ([dict(v, sku=k) for k, v in catalog.items() if isinstance(v, dict)]
                    if isinstance(catalog, dict) else catalog)
        query = str(args.get('query', '')).strip().lower()
        category = str(args.get('category', '')).strip().lower()
        shop = str(args.get('shop', '')).strip().lower()
        skus = {p.get('sku') for p in products if isinstance(p, dict)
                and (not query or query in json.dumps(p, ensure_ascii=False).lower())
                and (not category or category in str(p.get('category', '')).lower())}
        out['personal_history'] = [t for t in reversed(trades)
            if t.get('result') == 'deal' and t.get('order') is not None
            and (not (query or category) or t.get('item') in skus)
            and (not shop or shop in str(t.get('shop', '')).lower())][:limit]
    return out


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    with open(os.path.join(md, 'catalog.json'), encoding='utf-8') as f:
        catalog = json.load(f)
    try:
        with open(os.environ.get('BUYER_MEMORY') or 'memory/market_memory_r3.json', encoding='utf-8') as f:
            trades = json.load(f).get('trades', [])
    except (OSError, ValueError):
        trades = []
    print(json.dumps(research(catalog, market_model.load_break_rows(md), trades, args), ensure_ascii=False))   # settles


if __name__ == '__main__':
    main()
