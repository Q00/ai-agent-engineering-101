"""Catalog SKU join with paid ledger rows; all supplied filters intersect."""
import json
import os
import sys
import market_model


def search(catalog, ledger, args):
    if isinstance(catalog, dict):
        products = [dict(v, sku=k) for k, v in catalog.items() if isinstance(v, dict)]
    else:
        products = [r for r in catalog if isinstance(r, dict)]
    query = str(args.get('query', '')).strip().lower()
    category = str(args.get('category', '')).strip().lower()
    shop = str(args.get('shop', '')).strip().lower()
    limit = max(1, min(100, int(args.get('limit', 20))))
    products = [r for r in products
                if (not query or query in json.dumps(r, ensure_ascii=False).lower())
                and (not category or category in str(r.get('category', '')).lower())]
    skus = {r.get('sku') for r in products if r.get('sku')}
    paid, seen = [], set()
    for r in ledger:
        if r.get('k') != 'settle' or r.get('seq') in seen:
            continue
        seen.add(r.get('seq'))
        if (query or category) and r.get('sku') not in skus:
            continue
        if shop and not any(shop in str(r.get(k, '')).lower() for k in ('shop', 'seller')):
            continue
        paid.append(r)
    paid.sort(key=lambda r: (r.get('ts', 0), r.get('seq', 0)), reverse=True)
    return {'catalog': products[:limit], 'ledger': paid[:limit],
            'matched_products': len(products), 'matched_paid': len(paid),
            'basis': 'deduplicated settle only; catalog SKU/category join; no buyer-as-shop match'}


def main():
    args = json.load(sys.stdin)
    md = os.environ['MARKET_DIR']
    with open(os.path.join(md, 'catalog.json'), encoding='utf-8') as f:
        catalog = json.load(f)
    print(json.dumps(search(catalog, market_model.load_break_rows(md), args), ensure_ascii=False))   # settle rows only


if __name__ == '__main__':
    main()
