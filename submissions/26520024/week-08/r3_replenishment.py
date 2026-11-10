"""Bounded stockout evidence and replenishment targets; no model or network calls."""
from collections import Counter, defaultdict
import math
import re
import unicodedata

from r3_common import load_policy

HORIZON = 20 * 60
WINDOW = 6 * 3600


def _normal(value):
    return unicodedata.normalize('NFKC', str(value)).casefold().strip()


def record_request(memory, task, text, catalog, inventory, candidates, now):
    """An inbound request is NOT a verified market order or a buyer identity."""
    query = _normal(text[:3000])
    tokens = set(re.findall(r'[\w-]+', query))
    named = [sku for sku in catalog if _normal(sku) in tokens]
    if not named:
        named = [sku for sku, product in catalog.items()
                 if len(_normal(product.get('name', ''))) >= 5
                 and _normal(product['name']) in query]
    if len(named) != 1:
        return False  # Ambiguous, numeric, and unknown requests are not stockout evidence.
    sku = named[0]
    held = [u for u in inventory['units'] if u['sku'] == sku]
    available = [u for u in candidates if u['sku'] == sku]
    status = 'matching_inventory' if available else 'unsellable_inventory' if held else 'stockout'
    return memory.record('seller_demand', str(task), {
        'task': str(task), 'sku': sku, 'category': catalog[sku]['category'],
        'status': status, 'matching_units': len(available),
        'source': 'unverified_inbound_request_not_paid_demand'}, ts=now)


def record_supply_gap(memory, order, rows, truncated, now):
    acceptable = order.get('acceptable', [])
    if truncated or not isinstance(acceptable, list) or not acceptable:
        return
    acceptable = sorted({sku for sku in acceptable if isinstance(sku, str)})
    if len(acceptable) > 1000:
        return
    stock = Counter()
    for row in rows:
        stock[row['sku']] += row['qty']
    memory.record('buyer_supply_gap', str(order['order']), {
        'order': str(order['order']), 'category': order['category'],
        'acceptable': acceptable, 'quantities': {s: stock[s] for s in acceptable},
        'scope': 'complete_received_shop_list_not_entire_market',
        'no_listed_substitute': not any(stock[s] for s in acceptable)}, ts=now)


def enrich(inputs, memory, inventory, now):
    if not load_policy(memory.md)['adaptive_replenishment']:
        return inputs
    sales, own, gaps = defaultdict(list), defaultdict(list), []
    from r3_discovery import _identities
    seen = set()
    for e in memory.events('market_settle', 10000):
        if 0 <= now - e['ts'] <= WINDOW and e['data'].get('settled') is not False:
            identities = _identities(e['data'])
            duplicate = bool(identities & seen)
            seen.update(identities)
            if duplicate:
                continue
            sales[e['data'].get('sku', e['data'].get('item'))].append(e)
    for e in memory.events('seller_settlement', 2000):
        if 0 <= now - e['ts'] <= WINDOW:
            own[e['data'].get('sku')].append(e)
    for e in memory.events('buyer_supply_gap', 1000):
        if 0 <= now - e['ts'] <= HORIZON:
            gaps.append(e)
    rows = {r['sku']: r for r in inputs['candidates']}
    for sku, row in rows.items():
        portfolio = row['portfolio']
        public = sales[sku]
        distinct = {e['data']['buyer'] for e in public if e['data'].get('buyer')}
        mature = [e for e in own[sku]
                  if now >= e['data'].get('warranty_matures_at', math.inf)
                  and e['data'].get('cost', 0) > 0
                  and e['data'].get('margin_after_storage', 0) >= .03 * e['data']['cost']]
        fresh = any(0 <= now - e['ts'] <= HORIZON for e in public)
        fast = any(e['data'].get('holding_hours', math.inf) <= HORIZON / 3600 for e in mature)
        cautious_margin = portfolio['estimated_margin'] - row['list_price'] * .025
        provisional = (not portfolio['scale_ready'] and bool(mature) and fast and fresh
                       and len(public) >= 3 and len(distinct) >= 2
                       and portfolio['observed_refunds'] == 0
                       and all(e['data'].get('margin_after_storage', 0) > 0 for e in own[sku])
                       and portfolio['surge_factor'] <= 1.10 and cautious_margin > 0)
        recent_own = sum(0 <= now - e['ts'] <= HORIZON for e in own[sku])
        target = (min(3, max(1, recent_own + 1)) if portfolio['scale_ready'] else
                  2 if provisional else 1)
        if portfolio['observed_refunds'] or any(e['data'].get('margin_after_storage', 0) <= 0 for e in own[sku]):
            target = 1
        alternatives = []
        for gap in gaps:
            data = gap['data']
            if (data.get('category') != row['category'] or sku not in data['acceptable']
                    or data['quantities'].get(sku, 0) > 1):
                continue
            for anchor in data['acceptable']:
                other = rows.get(anchor)
                if (anchor == sku or not other or other['category'] != row['category']
                        or not .75 <= row['list_price'] / other['list_price'] <= 1.25
                        or data['quantities'].get(anchor, 0) > 1):
                    continue
                anchor_sales = sales[anchor]
                buyers = {e['data']['buyer'] for e in anchor_sales if e['data'].get('buyer')}
                if (len(anchor_sales) >= 3 and len(buyers) >= 2 and cautious_margin > 0
                        and portfolio['surge_factor'] <= other['portfolio']['surge_factor']):
                    alternatives.append({'anchor': anchor, 'order': data['order'],
                                         'source': 'observed_coacceptable_order_not_semantic_relation'})
        unique = {(a['anchor'], a['order']): a for a in alternatives}
        row['replenishment'] = {
            'horizon_seconds': HORIZON, 'target_units': target, 'provisional_scale': provisional,
            'recent_own_settlements': recent_own, 'recent_public_sales': len(public),
            'distinct_public_buyers': len(distinct), 'risk_buffered_margin': round(cautious_margin, 2),
            'substitution_evidence': list(unique.values())[:4],
            'basis': 'bounded_heuristic_not_forecast; unknown_exposure_is_not_zero_demand'}
        # Supply-gap claims affect ranking only, never the independent scaling gate.
        if unique:
            row['portfolio_score'] = round(row['portfolio_score'] * 1.25, 6)
    return inputs


def summarize(events, now, seconds=7200):
    recent = [e for e in events if 0 <= now - e['ts'] < seconds]
    requests = {e['data']['task']: e['data'] for e in recent if e['kind'] == 'seller_demand'}
    gaps = {e['data']['order']: e['data'] for e in recent if e['kind'] == 'buyer_supply_gap'}
    predictions = [e for e in recent if e['kind'] == 'restock_prediction']
    sales = [e for e in recent if e['kind'] == 'seller_settlement']
    planned = {r['sku'] for e in predictions for r in e['data'].get('buy', [])}
    linked = [e for e in sales if e['data']['sku'] in planned and any(
        p['ts'] <= e['ts'] and e['data']['sku'] in {r['sku'] for r in p['data'].get('buy', [])}
        for p in predictions)]
    return {'unique_requests': len(requests),
            'stockout_requests': sum(r['status'] == 'stockout' for r in requests.values()),
            'unsellable_requests': sum(r['status'] == 'unsellable_inventory' for r in requests.values()),
            'buyer_orders_without_listed_substitute': sum(g['no_listed_substitute'] for g in gaps.values()),
            'planned_units_not_purchase_proof': sum(r['qty'] for e in predictions for r in e['data'].get('buy', [])),
            'subsequent_same_sku_settlements': len(linked),
            'subsequent_margin_before_refunds': sum(e['data'].get('margin_after_storage', 0) for e in linked),
            'limitations': 'Requests are unverified; partial shop coverage; no lost-profit estimate or causal lift. '
                          'Plan is not fulfillment; same SKU is not unit-level purchase attribution.'}
