"""Bounded merchant context: discoverability, available exposure and paid conversion.

Own buyer baskets describe ONLY our observed orders, not global hidden demand.
Public settlements are realized trades; equal traffic share is a planning prior.
"""
import hashlib
import math
import time
from collections import Counter, defaultdict
from .market_memory import market_book

HORIZON = 21600


def observe_inventory(journal, inv, now=None):
    now = time.time() if now is None else now
    if inv.get('round') != 3:
        return
    counts = dict(Counter(u['sku'] for u in inv.get('units', []) if
                          not u.get('expires_at') or u['expires_at'] > now))
    history = journal.get('merchant_stock_timeline', [])
    if history and history[-1]['counts'] == counts and now-history[-1]['at'] < 30:
        return
    history = [r for r in history if now-HORIZON <= r['at'] <= now]
    history.append({'at': now, 'counts': counts})
    journal.put('merchant_stock_timeline', history[-1024:])


def coverage_keys(sku, product, baskets):
    category = product.get('category', '')
    band = int(math.log2(max(1, product.get('list_price', 1))))
    return ['category:' + category, 'band:' + category + ':' + str(band)] + [
        key for key, row in baskets.items() if sku in row['skus']]


def context(journal, catalog, now=None):
    now = time.time() if now is None else now
    cached = journal.get('merchant_context_v5', {})
    if 0 <= now-cached.get('at', -1000) < 30:
        return cached
    book = {s: dict(row, samples=row.get('external_samples', row['samples']))
            for s,row in market_book(journal, catalog, now).items()}
    timeline = journal.get('merchant_stock_timeline', [])
    exposure, coverage_start, intervals = Counter(), now, defaultdict(list)
    if timeline:
        coverage_start = max(now-HORIZON, timeline[0]['at'])
        for index, row in enumerate(timeline):
            finish = min(now, timeline[index+1]['at'] if index+1 < len(timeline) else now)
            # A gap in sampling is unknown availability, never assumed uptime.
            seconds = max(0, min(finish-row['at'], 60))
            if now-HORIZON <= row['at'] <= now:
                for sku, qty in row['counts'].items():
                    if qty > 0:
                        exposure[sku] += seconds/3600
                        intervals[sku].append((row['at'], row['at']+seconds))
    visits = [e for e in journal.events('customer_visit', 2000) if 0 <= now-e['_ts'] <= HORIZON]
    opportunities = {e['task']: e for e in journal.events('shop_opportunity', 10000)
                     if coverage_start <= e['_ts'] <= now}
    stockouts = {e['task'] for e in journal.events('customer_outcome', 8000)
                 if e.get('outcome') == 'stockout' and 0 <= now-e['_ts'] <= HORIZON}
    available_requests, lost, category_lost = Counter(), Counter(), Counter()
    lost_seen=set()
    for e in visits:
        if (e['task'] in opportunities and e.get('sku') and
                any(a <= e['_ts'] <= b for a,b in intervals[e['sku']])):
            available_requests[e['sku']] += 1
        if e['task'] in stockouts:
            if e.get('sku'):
                lost[e['sku']] += 1
            if e.get('category'):
                key=(e['category'],int(e['_ts']//60),e.get('signer') or 'anonymous')
                if key not in lost_seen:
                    category_lost[e['category']] += math.exp(-(now-e['_ts'])/3600)
                    lost_seen.add(key)
    baskets = {}
    for e in journal.events('buyer_order', 500):
        skus = sorted(s for s in e.get('acceptable', []) if s in catalog)
        if not skus or not 0 <= now-e['_ts'] <= HORIZON:
            continue
        key = 'own-basket:' + hashlib.sha256('|'.join(skus).encode()).hexdigest()[:12]
        if key not in baskets and len(baskets) >= 100:
            continue
        row = baskets.setdefault(key, {'skus': skus, 'weight': 0})
        # Our order describes an acceptable set, but our shop cannot serve it.
        # Retain the set for interpreting coverage, with ZERO demand weight.
        row['weight'] += 0
    weights = Counter({key: row['weight'] for key,row in baskets.items()})
    for sku, row in book.items():
        for key in coverage_keys(sku, catalog[sku], {}):
            weights[key] += row['samples'] / 6 * min(10, math.sqrt(catalog[sku].get('list_price',1)/10000))
    for category, count in category_lost.items():
        weights['category:' + category] += 2 * count
    competition = journal.snapshot('market_stock_counts.json', {})
    live_counts = competition.get('counts', {}) if 0 <= now-competition.get('at', 0) < 120 else {}
    rows = {}
    relevant = set(book) | set(available_requests) | set(lost)
    # All observed own baskets remain eligible, but their signal is weak and
    # an unknown SKU still spends only from the bounded exploration budget.
    relevant.update(sku for basket in baskets.values() for sku in basket['skus'])
    settled_by_sku=defaultdict(set)
    positive_paid=defaultdict(set)
    for e in journal.events('shop_settled',10000):
        if coverage_start<=e['_ts']<=now and any(a<=e['_ts']<=b for a,b in intervals[e.get('sku')]):
            settled_by_sku[e.get('sku')].add(e.get('task'))
            if type(e.get('cost')) in (int,float) and e['price']>e['cost']:
                positive_paid[e.get('sku')].add(e.get('task'))
    mature_by_sku=defaultdict(set)
    for e in visits:
        if now-e['_ts']>=300 and e['task'] in opportunities:
            mature_by_sku[e.get('sku')].add(e['task'])
    for sku in relevant:
        if sku not in catalog:
            continue
        public = book.get(sku, {})
        competitors = max(1, live_counts.get(sku, public.get('sellers', 0)) + 1)
        prior = .5 * public.get('samples', 0) / 6 / competitors
        observed_hours = exposure.get(sku, 0)
        n = available_requests[sku]
        settled_tasks=settled_by_sku[sku]
        mature_requests=mature_by_sku[sku]
        paid_scenario=(len(mature_requests & settled_tasks)+1)/(len(mature_requests)+2)
        conditional = ((n + .25*prior)/(observed_hours + .25)
                       if observed_hours >= 2/60 and n >= 2 else prior)
        keys=coverage_keys(sku, catalog[sku], baskets)
        for buyer,count in public.get('external_buyers',[]):
            key='observed-buyer:'+buyer+':'+catalog[sku].get('category','')
            keys.append(key)
            weights[key]+=count/6
        # The public prior is realized sales; own requests need conversion.
        rate=max(prior,conditional*paid_scenario) if observed_hours>=2/60 and n>=2 else prior
        rows[sku] = {'public': public, 'arrival_rate_scenario': min(120, rate),
                     'paid_while_available':len(settled_tasks),
                     'positive_paid_while_available':len(positive_paid[sku]),
                     'paid_arrival_rate_scenario':min(120,(len(positive_paid[sku])+.25*prior)/(.25+observed_hours))
                         if observed_hours>=2/60 else prior,
                     'payment_rate_scenario':paid_scenario,
                     'available_hours_observed': observed_hours, 'available_requests': n,
                     'lost_exact_requests': lost[sku], 'category_lost_signal': category_lost[catalog[sku].get('category')],
                     'coverage_keys': keys,
                     'competition_sellers_plus_us': competitors,
                     'rate_note': 'equal-share prior x0.5, shrunk available-time requests; not a causal demand estimate'}
    # Persist compact aggregates; never grow an unbounded stream of raw chats.
    selected = sorted(rows, key=lambda s: (rows[s]['lost_exact_requests'], rows[s]['public'].get('samples', 0),
                                           rows[s]['available_requests'], s), reverse=True)[:1500]
    result = {'at': now, 'skus': {s: rows[s] for s in selected}, 'coverage_weights': dict(weights),
              'summary': {'tracked_skus': len(selected), 'observed_baskets': len(baskets),
                          'top_unserved_categories': category_lost.most_common(10),
                          'exposure_started_at': coverage_start,
                          'context_note': 'own buyer baskets have zero demand weight; external settlements only'}}
    journal.put('merchant_context_v5', result)
    return result
