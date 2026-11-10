"""Public cash leaders and repeated purchases, not inferred private alliances."""
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
import sqlite3
import statistics
from urllib.parse import urlsplit

from r3_common import (atomic_json, effective_grade, load_catalog, load_inventory,
                       load_policy, read_json)

WINDOW = 6 * 3600
FRESH = 1800
MAX_ROWS = 10000


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def url_key(value):
    if not isinstance(value, str) or len(value) > 500:
        return None
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
        return None
    return value.rstrip('/')


def capture(board, shops, name, now):
    if not isinstance(board, dict) or board.get('round') != 3:
        raise ValueError('Round 3 board required')
    if not isinstance(board.get('shops'), list) or not isinstance(board.get('buyers'), list):
        raise ValueError('Public identity rows required')
    identities, buyers = [], {}
    fields = ('profit', 'book', 'revenue', 'deals', 'stars', 'stock_cost', 'stock_units',
              'refunds', 'broken', 'claims', 'honest_claims')
    for row in board['shops'][:500]:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            continue
        owner = row.get('owner') or {}
        owner = owner.get('login') if isinstance(owner, dict) else None
        card = row.get('card') or {}
        url = url_key(card.get('url')) if isinstance(card, dict) else None
        identities.append(dict(name=row['name'][:160], owner=owner.casefold() if isinstance(owner, str) else None,
            url=url, house=row.get('house') is True, alive=row.get('alive') is True,
            **{k: row[k] for k in fields if finite(row.get(k))}))
    for row in board['buyers'][:500]:
        if isinstance(row, dict) and isinstance(row.get('name'), str):
            owner = row.get('owner') or {}
            login = owner.get('login') if isinstance(owner, dict) else None
            buyers[row['name']] = login.casefold() if isinstance(login, str) else None
    own_owner = next((r['owner'] for r in identities if r['name'] == name), None)
    eligible = [r for r in identities if not r['house'] and finite(r.get('profit'))
                and r['name'] != name and (not own_owner or r['owner'] != own_owner)]
    leaders = sorted(eligible, key=lambda r: (-r['profit'], r['name']))[:2]
    stocks, aliases = {}, []
    for shop in shops if isinstance(shops, list) else []:
        if not isinstance(shop, dict):
            continue
        matching = [r for r in identities if r['name'] == shop.get('name')]
        current_url = url_key(shop.get('url'))
        if len(matching) == 1 and current_url:
            aliases.append({'name': matching[0]['name'], 'owner': matching[0]['owner'],
                            'url': current_url, 'source': 'public_shop_directory'})
        if shop.get('name') not in {r['name'] for r in leaders}:
            continue
        stock = shop.get('stock')
        if isinstance(stock, dict):
            rows = [(sku, qty) for sku, qty in stock.items()
                    if isinstance(sku, str) and type(qty) is int and qty > 0]
            stocks[shop['name']] = {'skus': dict(rows[:2000]), 'truncated': len(rows) > 2000}
    return {'at': now, 'own_name': name, 'own_owner': own_owner, 'identities': identities,
            'buyer_owners': buyers, 'leaders': leaders, 'stocks': stocks, 'url_aliases': aliases,
            'ranking': 'public_non_house_shops_by_Cash_profit_excluding_own_account',
            'cash_definition': 'balance minus start capital and bankruptcy support, not raw balance'}


def build_report(current, histories, events, catalog, now, previous=None):
    mappings = defaultdict(set)
    for snapshot in [current, *histories]:
        if not isinstance(snapshot, dict) or not finite(snapshot.get('at')) or not 0 <= now-snapshot['at'] <= 7*86400:
            continue
        for row in snapshot.get('identities', []) + snapshot.get('url_aliases', []):
            if row.get('url'):
                mappings[row['url']].add((row.get('owner'), row['name']))
    identities = {r['name']: r for r in current['identities']}
    leaders = {r['name']: r for r in current['leaders']}
    resolved, seen, unresolved = [], set(), 0
    for event in sorted(events, key=lambda e: e['ts']):
        data, stamp = event['data'], event['ts']
        if event['kind'] != 'market_settle' or not finite(stamp) or not 0 <= now-stamp < WINDOW:
            continue
        if data.get('source') != 'public_ledger':
            continue
        sku, buyer, seq = data.get('sku', data.get('item')), data.get('buyer'), data.get('seq')
        amount = data.get('amount', data.get('price'))
        if (sku not in catalog or not isinstance(buyer, str) or not buyer or type(seq) is not int
                or not finite(amount) or amount < 0):
            continue
        keys = {('seq', seq)} | {(key, data[key]) for key in ('order', 'unit')
                                if isinstance(data.get(key), str) and data[key]}
        if seen & keys:
            continue
        seen.update(keys)
        names = mappings.get(url_key(data.get('shop')), set())
        if len(names) != 1:
            unresolved += 1
            continue
        owner, shop = next(iter(names))
        # A URL/name reused by another owner is not a stable identity.
        if shop not in identities or owner != identities[shop]['owner']:
            unresolved += 1
            continue
        buyer_owner = current['buyer_owners'].get(buyer)
        if owner and buyer_owner == owner:
            continue
        resolved.append(dict(seq=seq, ts=stamp, sku=sku, category=catalog[sku]['category'],
            buyer=buyer, buyer_owner=buyer_owner, shop=shop, amount=amount,
            grade=data.get('grade'), honest=data.get('claim_honest') is True,
            warranty=data.get('warranty_hours')))
    buyer_totals = Counter(r['buyer'] for r in resolved)
    profiles, edges, products = [], [], defaultdict(list)
    previous_leaders = {r['name']: r for r in (previous or {}).get('leaders', [])}
    for name, leader in leaders.items():
        rows = [r for r in resolved if r['shop'] == name]
        groups = defaultdict(list)
        for row in rows:
            groups[row['buyer']].append(row)
            products[row['sku']].append(row)
        for buyer, trades in groups.items():
            if len(trades) < 3 or (current['own_owner'] and current['buyer_owners'].get(buyer) == current['own_owner']):
                continue
            edges.append({'shop': name, 'buyer': buyer, 'count': len(trades),
                'buyer_observed_share': round(len(trades)/buyer_totals[buyer], 4),
                'categories': dict(Counter(r['category'] for r in trades).most_common(5)),
                'refs': [r['seq'] for r in trades[-5:]],
                'evidence': 'repeated_settlements_only_not_proof_of_partnership'})
        old = previous_leaders.get(name, {})
        elapsed = now-(previous or {}).get('at', now)
        delta = {k: leader[k]-old[k] for k in ('profit', 'book', 'revenue', 'deals')
                 if finite(leader.get(k)) and finite(old.get(k)) and 60 <= elapsed <= 4*3600}
        profiles.append(dict(leader, sample_deals=len(rows), distinct_buyers=len(groups),
            categories=dict(Counter(r['category'] for r in rows).most_common(6)),
            sampled_revenue=sum(r['amount'] for r in rows), observed_delta=delta,
            delta_seconds=elapsed if delta else None,
            stock_snapshot=current['stocks'].get(name),
            limitation='Cash does not identify margin, loans, restock policy or private coordination.'))
    opportunities = []
    for sku, rows in products.items():
        if len(rows) < 3:
            continue
        grouped = defaultdict(list)
        for row in rows:
            if row['honest'] and row['grade'] in ('A', 'B', 'C') and row['warranty'] in (None, 0):
                grouped[row['grade']].append(row['amount'])
        customers = Counter(r['buyer'] for r in rows if not current['own_owner'] or
                            r['buyer_owner'] != current['own_owner'])
        opportunities.append({'sku': sku, 'category': catalog[sku]['category'],
            'settlements': len(rows), 'distinct_buyers': len(customers),
            'repeated_buyers': [b for b, n in customers.most_common(5) if n >= 3],
            'refs': [r['seq'] for r in rows[-5:]],
            'grade_price': {g: {'median': statistics.median(v), 'samples': len(v)}
                            for g, v in grouped.items() if len(v) >= 3},
            'median_paid_list': round(statistics.median(r['amount']/catalog[sku]['list_price'] for r in rows), 4),
            'scope': 'sampled_top_cash_shop_sales_not_future_orders'})
    opportunities.sort(key=lambda r: (-r['distinct_buyers'], -r['settlements'], r['sku']))
    arrivals = []
    for edge in edges:
        rival_times = [r['ts'] for r in resolved if r['shop']==edge['shop'] and r['buyer']==edge['buyer']]
        own = [r for r in resolved if r['shop']==current['own_name'] and r['buyer']==edge['buyer']
               and r['ts'] > sorted(rival_times)[2]]
        if own:
            arrivals.append({'buyer': edge['buyer'], 'prior_shop': edge['shop'],
                             'own_settlements': len(own), 'refs': [r['seq'] for r in own[-5:]]})
    newest = max((r['ts'] for r in resolved), default=None)
    return {'at': now, 'snapshot_at': current['at'], 'ranking': current['ranking'],
        'own_name': current['own_name'], 'leaders': profiles,
        'relationships': sorted(edges, key=lambda r: (-r['count'], r['shop'], r['buyer']))[:40],
        'opportunities': opportunities[:100], 'leader_then_our_shop': arrivals[:20],
        'coverage': {'window_seconds': WINDOW, 'sample_count': len(resolved),
                     'newest_settlement_at': newest, 'unresolved_urls': unresolved,
                     'row_limit_hit': len(events) >= MAX_ROWS, 'complete_market': False},
        'limits': ['Observed overlap is not a partnership or proof that our campaign caused a switch.',
                   'Historical URL aliases are linked only when public owner/name mappings agree.',
                   'No private budgets, reviews, forum instructions or paid votes determine these actions.']}


def refresh(memory, board, shops, now):
    md = memory.md
    if not load_policy(md)['competitive_tracking_enabled']:
        return
    current = capture(board, shops, os.environ.get('R3_SHOP_NAME', 'leeuichann'), now)
    histories = [e['data'] for e in memory.events('competition_snapshot', 300)]
    rows = memory.db.execute("SELECT * FROM events WHERE kind='market_settle' AND ts>=? AND ts<=? "
                             "ORDER BY ts DESC LIMIT ?", (now-WINDOW, now, MAX_ROWS))
    events = [memory._event(row) for row in rows]
    report = build_report(current, histories, events, load_catalog(md), now,
                          read_json(md/'competition-latest.json', {}))
    memory.record('competition_snapshot', str(now), current, ts=now)
    memory.record('competition_report', str(now), report, ts=now)
    atomic_json(md/'competition-latest.json', report)
    return report


def fresh_report(md, now):
    if not load_policy(md)['competitive_tracking_enabled']:
        return {}
    try:
        report = read_json(md/'competition-latest.json', {})
    except (OSError, ValueError):
        return {}
    if not isinstance(report, dict):
        return {}
    stamp = report.get('snapshot_at')
    newest = report.get('coverage', {}).get('newest_settlement_at')
    if not finite(stamp) or not finite(newest) or not 0 <= now-stamp <= FRESH or not 0 <= now-newest <= FRESH:
        return {}
    return report


def enrich(inputs, report):
    opportunities = {r['sku']: r for r in report.get('opportunities', [])}
    for row in inputs['candidates']:
        anchor = opportunities.get(row['sku'])
        direct = anchor is not None
        if anchor is None and row.get('demand_status') in ('eligible_unseen', 'related_unseen'):
            anchor = next((opportunities[s] for s in row.get('related_skus', []) if s in opportunities), None)
        if anchor is None:
            continue
        # A minority reweight, never independent authority to buy or expand exposure.
        diverse = anchor['distinct_buyers'] >= 2
        bonus = (.2 if direct else .1) if diverse else 0
        positive_margin = row.get('portfolio', {}).get('estimated_margin', 0) > 0
        row['portfolio_score'] = row.get('portfolio_score', row['score']) * (1+bonus if positive_margin else 1)
        row['competitive_evidence'] = {'anchor_sku': anchor['sku'], 'sample_deals': anchor['settlements'],
            'distinct_buyers': anchor['distinct_buyers'], 'refs': anchor['refs'],
            'scope': 'same_sku_observed' if direct else 'related_hypothesis_not_proven_demand',
            'weight_bonus': bonus if positive_margin else 0}
    return inputs


def ceiling(unit, bounds, report, now, grade):
    opportunity = next((r for r in report.get('opportunities', []) if r['sku']==unit['sku']), None)
    if not opportunity or bounds['liquidation']:
        return bounds
    sample = opportunity.get('grade_price', {}).get(grade)
    if not sample or sample['samples'] < 3 or opportunity['distinct_buyers'] < 2:
        return bounds
    # No blind race to zero: require cost/storage +3%, and cut at most 5% of our ask.
    target = math.floor(sample['median'] * .98)
    floor = max(bounds['minimum'], math.ceil(unit['cost']*1.03), math.ceil(bounds['ceiling']*.95))
    if not floor <= target < bounds['ceiling']:
        return bounds
    return dict(bounds, ceiling=target, competitive_pricing={
        'basis': 'same_sku_grade_honest_zero_warranty_paid_sample', 'samples': sample['samples'],
        'median': sample['median'], 'refs': opportunity['refs'], 'margin_floor': floor})


def promotion(memory, md, now):
    report = fresh_report(md, now)
    if not report:
        return []
    queued = memory.events('forum_queued', 1000)
    if any(e['key'].startswith('stock-') and now-e['ts'] < 3600 for e in queued):
        return []
    try:
        inventory, catalog = load_inventory(md, now=now), load_catalog(md)
        dbfile = memory.dbdir/'r3_seller.sqlite3'
        if not dbfile.is_file():
            return []
        with sqlite3.connect(dbfile.as_uri()+'?mode=ro', uri=True, timeout=1) as db:
            held = {r[0] for r in db.execute('SELECT unit FROM deals WHERE unit IS NOT NULL')}
    except (OSError, ValueError, sqlite3.Error):
        return []
    for row in report['opportunities']:
        if not row['repeated_buyers']:
            continue
        units = [u for u in inventory['units'] if u['sku']==row['sku'] and u['unit'] not in held
                 and u.get('cert') and effective_grade(u,catalog,now) != 'expired'
                 and (u['kind'] != 'food' or u['expires_at']-now >= 6*3600)]
        if not units:
            continue
        # Do not name a buyer as a partner, promise a discount, or barter for reviews.
        key = hashlib.sha256(json.dumps(sorted(u['unit'] for u in units)).encode()).hexdigest()[:20]
        grades = sorted({effective_grade(u,catalog,now) for u in units})
        text = (f"{report['own_name']} stock update for buyers comparing {row['category']}: "
                f"SKU {row['sku']}, {len(units)} unreserved unit(s), grade(s) {','.join(grades)} "
                'at this snapshot. Public paid purchases of this SKU are linked below; '
                'they are not endorsements of our shop. Ask us by SKU for a current signed quote. '
                'Availability can change; warranty is 0 hours. Reviews are independent of price.')
        return [{'id': 'stock-'+key, 'post': text, 'refs': row['refs'][:3], 'as': 'shop'}]
    return []
