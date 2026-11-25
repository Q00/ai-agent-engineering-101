"""Continuous market diagnosis feeding bounded, audited strategy trials.

This observes; adaptive.py owns compare-and-swap trials and mature promotion.
No peer/forum text becomes executable policy. Hard cash/checkout guards remain.
"""
import hashlib
import time
import re
from collections import Counter
from .finance import book_row
from .economy import cash_capacity
from .state import policy, atomic_json


def audit_rules(journal, text, now=None):
    now = time.time() if now is None else now
    understood = ('shops by book, revenue and stars' in text and
                  'each further unit adds 0.2%' in text and 'up to +50%' in text)
    buyer_metric=('saved' if 'buyers by saved rate' in text and 'divided by deals + 100' in text else
                  'surplus' if 'buyers by surplus' in text else 'unknown')
    flat=' '.join(text.split())
    buyer_weights_ok=bool(re.search(r'buyers by .*?ALL weighs them 0\.5,\s*0\.3,\s*0\.1,\s*0\.1',flat))
    from .buyer_scoring import LEGACY,MARGIN
    score_formula=(MARGIN if '(list price - price) / (list price - unit cost)' in flat and 'at most 1' in flat else
                   LEGACY if 'surplus / list price' in flat or '(list price - price) / list price' in flat else 'unknown')
    saved_formula_ok=('divided by deals + 100' in flat and 'counts -1, or 0 if refunded' in flat
                      and score_formula!='unknown')
    forum_mode=('raw' if 'except the forum rating, which counts as itself (0 to 1)' in flat else
                'unknown' if 'except the forum rating' in flat else 'percentile')
    paid_power='A dislike with value -N' in flat
    power_known=(not paid_power or all(s in flat for s in
                 ['N up to 1000','(N - 1)^2','3%','largest Book','power_fee','next_vote_fee']))
    result = {'at': now, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
              'compatible': understood, 'expected': 'book/revenue/stars; surge<=1.5',
              'source': '/agent.md','buyer_metric':buyer_metric,'buyer_saved_formula':score_formula,
              'buyer_compatible':buyer_weights_ok and forum_mode!='unknown' and
                  (buyer_metric=='surplus' or buyer_metric=='saved' and saved_formula_ok),
              'buyer_forum_metric':forum_mode,'paid_dislike_power':paid_power,
              'paid_dislike_contract_known':power_known,
              'forum_vote_min_deals':50 if 'at least 50 settled deals' in text else 5,
              'stance_scoring':'one stance per' in text,'dislike_costs_shop_cash':'next_dislike_fee' in text}
    previous = journal.get('market_rules', {})
    result['buyer_score_epoch']=(previous.get('buyer_score_epoch',now) if
        previous.get('buyer_saved_formula')==score_formula else now)
    journal.put('market_rules', result)
    if previous.get('sha256') != result['sha256']:
        journal.add('market_rules_observed', result)
    if previous.get('buyer_saved_formula')!=score_formula:
        journal.add('buyer_score_formula_changed',{'before':previous.get('buyer_saved_formula'),
                    'after':score_formula,'at':now,'rules_hash':result['sha256']},role='buyer')
    if previous.get('buyer_forum_metric')!=forum_mode:
        journal.add('market_scoring_definition',{'at':now,'before':previous.get('buyer_forum_metric'),
                    'after':forum_mode,'buyer_metric':buyer_metric,'paid_dislike_power':paid_power})
    return result


def update(journal, config=None, now=None, force=False):
    now = time.time() if now is None else now
    cfg = config or policy()
    previous=journal.get('market_control',{})
    interval=30 if previous.get('pause_restock') else cfg.get('market_control_interval_seconds',300)
    with journal.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT value FROM kv WHERE key='market_control:last_tick'").fetchone()
        if row and not force and now-float(row[0]) < interval:
            return journal.get('market_control', {})
        c.execute("INSERT OR REPLACE INTO kv VALUES('market_control:last_tick',?)", (str(now),))
    board = journal.snapshot('board.json', {})
    inv = journal.snapshot('inventory.json', {})
    if board.get('round') != 3 or inv.get('round') != 3:
        return {'status': 'waiting_for_round3'}
    catalog = journal.snapshot('catalog.json', {})
    from .merchant import context
    market = context(journal, catalog, now,config=cfg)
    own = next((r for r in board.get('shops', []) if r.get('name')=='seochanit'), {})
    buyer = next((r for r in board.get('buyers', []) if r.get('name')=='seochanit'), {})
    current_book = book_row(own)
    epoch = journal.get('objective_epoch', now)
    old = journal.get('market_control', {})
    peak = max(current_book, old.get('book_peak', current_book)) if old.get('epoch')==epoch else current_book
    cap = cash_capacity(inv, journal, cfg, now)
    next_open=(inv.get('restock') or {}).get('opens_at',now)
    cap['reserve_release_before_next_supply']=sum(r['amount'] for r in cap['reserve_release_schedule'] if r['at']<=next_open)
    sales = [e for e in journal.events('shop_settled', 10000) if 0 <= now-e['_ts'] <= 21600]
    known = [e for e in sales if type(e.get('cost')) in (int,float)]
    losses = [e for e in known if e['price'] < e['cost']]
    shop_epoch=max(epoch,journal.get('shop_revision_epoch',epoch))
    current_known=[e for e in known if e['_ts']>=shop_epoch]
    current_losses=[e for e in current_known if e['price']<e['cost']]
    payments = [e for e in journal.events('payment', 10000) if 0 <= now-e['_ts'] <= 21600]
    failures = Counter('shelf_life' if 'shelf life' in e.get('reason','') else
                       'stockout' if 'not in stock' in e.get('reason','') else 'other'
                       for e in payments if not e.get('paid'))
    health = journal.snapshot('shop-health.json', {})
    rules = journal.get('market_rules', {})
    rules_ok = rules.get('compatible') is True and 0 <= now-rules.get('at',0) < 7200
    fresh = 0 <= now-journal.get('board_checked_at', 0) < 300
    if (cfg.get('shop_championship_enabled') and fresh and health and
            now-health.get('checked_at',0)>=120 and own.get('alive') is True and
            (own.get('card') or {}).get('url')):
        from .transport_health import refresh
        health=refresh(journal,own,now) or health
    healthy = not health or health.get('status')=='healthy' and 0<=now-health.get('checked_at',0)<120
    drawdown = max(0, peak-current_book)
    breached = drawdown>max(500000,abs(peak)*.15)
    brake_until=old.get('drawdown_brake_until',0) if old.get('epoch')==epoch else 0
    brake_drawdown=old.get('brake_drawdown',0) if old.get('epoch')==epoch else 0
    if breached and (not brake_until or drawdown>brake_drawdown+250000):
        brake_until=now+1800
        brake_drawdown=drawdown
    capital_fraction=.25 if breached and now>=brake_until else 1
    reasons=[]
    if not rules_ok:reasons.append('rules_unverified_or_changed')
    if not fresh:reasons.append('stale_board')
    feed = journal.get('public_feed_caught_up_at')
    if feed is None or not 0<=now-feed<180:reasons.append('ledger_lag_or_unverified')
    if not healthy:reasons.append('transport_unhealthy')
    if own.get('alive') is False:reasons.append('market_shop_not_discoverable')
    if breached and now<brake_until:reasons.append('book_drawdown_cooldown')
    if inv.get('balance', 0) < cap['cash_floor']+cap['refund_liability']:reasons.append('cash_obligations')
    skus = market.get('skus', {})
    opportunities = sorted([dict(sku=s, **{k:r.get(k) for k in
        ['arrival_rate_scenario','competition_sellers_plus_us','lost_exact_requests','available_hours_observed']})
        for s,r in skus.items() if r.get('public',{}).get('external_samples', r.get('public',{}).get('samples',0))>=3],
        key=lambda r:(r.get('arrival_rate_scenario') or 0),reverse=True)[:20]
    suggestions=[]
    if len(current_known)>=5 and len(current_losses)/len(current_known)>.1:
        suggestions.append({'role':'shop','field':'shop_margin_fraction','direction':1,'reason':'realized below-cost sales'})
    if market.get('summary',{}).get('top_unserved_categories'):
        suggestions.append({'role':'shop','field':'assortment_coverage_fraction','direction':1,'reason':'observed unserved external requests'})
    if len({u['sku'] for u in inv.get('units',[])})<12 and cap['spendable']>500000:
        suggestions.append({'role':'shop','field':'inventory_target_units','direction':1,'reason':'limited breadth with deployable capital'})
    if failures:
        suggestions.append({'role':'buyer','field':'buyer_food_time_seconds','direction':-1,'reason':'avoid repeated costly unsuitable food searches'})
    leaders=sorted([p for p in board.get('players',[]) if not p.get('house') and
                    type(p.get('total')) in (int,float)],key=lambda p:p['total'],reverse=True)
    result={'at':now,'epoch':epoch,'status':('defensive' if reasons else 'limited_recovery' if breached else 'observe_and_trade'),
            'book':current_book,'book_peak':peak,'book_drawdown':drawdown,
            'drawdown_brake_until':brake_until,'brake_drawdown':brake_drawdown,'capital_fraction':capital_fraction,
            'cash_capacity':cap,'pause_restock':bool(reasons),'reasons':reasons,
            'own_all':own.get('all',0)+buyer.get('all',0),
            'leader_total':leaders[0]['total'] if leaders else None,
            'leader_gap':leaders[0]['total']-own.get('all',0)-buyer.get('all',0) if leaders else None,
            'sold_known_cost':len(known),'below_cost_sales':len(losses),
            'shop_evidence_since':shop_epoch,'current_shop_known_cost':len(current_known),
            'current_shop_below_cost':len(current_losses),
            'buyer_payment_failures':dict(failures),'opportunities':opportunities,
            'suggestions':suggestions,'market_summary':market.get('summary',{}),
            'note':'observational diagnosis; policy trials require mature own evidence and rollback checks'}
    if cfg.get('shop_championship_enabled') or cfg.get('buyer_championship_enabled'):
        from .progress import observe
        result['championship']=observe(journal,board,inv,cap,now)
        # A shelf-life refusal is an eligibility/routing issue. Reducing all
        # food search time because any old payment failed is not supported.
        result['suggestions']=[s for s in result['suggestions'] if
                              not (s['role']=='buyer' and s['field']=='buyer_food_time_seconds')]
    journal.put('market_control', result)
    journal.add('market_control', result)
    atomic_json(journal.md/'market-control.json', result)
    if cfg.get('system_framework_enabled'):
        from .evolution import update as framework_update
        framework_update(journal,cfg,now)
    return result
