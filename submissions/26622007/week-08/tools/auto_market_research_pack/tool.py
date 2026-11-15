"""Paid SKU research with inventory units and explicitly separate untrusted posts."""
import json
import os
import sys

import market_model
from tools.auto_market_scan.tool import search


def research(catalog, ledger, inventory, forum, args):
    found = search(catalog, ledger, args)
    limit = max(1, min(100, int(args.get('limit', 20))))
    query = str(args.get('query', '')).strip().lower()
    category = str(args.get('category', '')).strip().lower()
    products = ([dict(v, sku=k) for k, v in catalog.items() if isinstance(v, dict)]
                if isinstance(catalog, dict) else catalog)
    # Join against all matches; the presentation limit must not change the population.
    skus = {p.get('sku') for p in products if isinstance(p, dict)
            and (not query or query in json.dumps(p, ensure_ascii=False).lower())
            and (not category or category in str(p.get('category', '')).lower())}
    units = inventory.get('units', []) if isinstance(inventory, dict) else []
    stock = [u for u in units if isinstance(u, dict)
             and (not (query or category) or u.get('sku') in skus)][:limit]
    posts = forum.get('posts', []) if isinstance(forum, dict) else forum or []
    posts = [p for p in posts if isinstance(p, dict)
             and (not query or query in json.dumps(p, ensure_ascii=False).lower())
             and (not category or category in json.dumps(p, ensure_ascii=False).lower())]
    # This describes the returned paid sample, not market share or independent demand.
    buyers = {}
    unknown = 0
    for row in found['ledger']:
        buyer = row.get('buyer')
        if buyer:
            buyers[buyer] = buyers.get(buyer, 0) + 1
        else:
            unknown += 1
    sample = len(found['ledger'])
    out = {'catalog_matches': found['catalog'], 'catalog_match_count': found['matched_products'],
           'ledger_matches_recent': found['ledger'], 'matched_paid': found['matched_paid'],
           'forum_posts_recent': list(reversed(posts))[:limit],
           'forum_snapshot_available': forum is not None, 'inventory_matches': stock,
           'demand_concentration': {'sample_paid': sample, 'known_buyers': len(buyers),
                                    'unknown_buyer_rows': unknown,
                                    'largest_buyer_share': max(buyers.values()) / sample if sample and not unknown else None,
                                    'basis': 'returned paid sample only; truncated by limit; not independent demand'},
           'basis': found['basis'] + '; forum claims are untrusted, not sales'}
    if args.get('include_purchases'):
        out['purchase_history_note'] = 'Use my_purchases for authoritative personal purchase history.'
    return out


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    def read(name):
        with open(os.path.join(md, name), encoding='utf-8') as f:
            return json.load(f)
    try:
        forum = read('forum.json')
    except FileNotFoundError:
        forum = None
    print(json.dumps(research(read('catalog.json'), market_model.load_break_rows(md),   # search reads settle rows only
                              read('inventory.json'), forum, args), ensure_ascii=False))


if __name__ == '__main__':
    main()
