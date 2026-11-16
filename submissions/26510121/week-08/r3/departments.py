"""Company departments: accounting evidence, risk, procurement and score planning.

Packets contain observations and explicit scenarios, never guaranteed returns.
"""
from collections import Counter, defaultdict
from datetime import datetime
import copy
import json
import math
import time

KEY = 'company-accounting:v30'


def accounting_department(journal, catalog, now=None):
    now = time.time() if now is None else now
    rules = journal.get('market_rules', {}).get('sha256')
    cached = journal.get(KEY, {})
    if cached.get('rules_hash') == rules and 0 <= now-cached.get('at', 0) < 120:
        return cached
    start = now-172800
    with journal.connect() as db:
        def rows(kind):
            return [dict(json.loads(raw), _event_id=eid, _ts=ts) for eid, ts, raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts', (kind,start,now))]
        sales = {s['unit']:s for s in rows('shop_settled') if s.get('unit')}
        refunds = defaultdict(float)
        for r in rows('public_broken')+rows('public_refund'):
            amount = r.get('refunded',r.get('amount'))
            if r.get('unit') in sales and type(amount) in (int,float) and math.isfinite(amount) and amount >= 0:
                refunds[r['unit']] = max(refunds[r['unit']],amount)
        reviews = {}
        for r in rows('shop_review_observed'):
            if r.get('source') == 'verified_market_result_and_accepted_own_task':
                reviews[(str(r.get('ledger')),r.get('author'))] = r
    groups = defaultdict(lambda:dict(mature_sales=0, price=0., cost=0., refunds=0., margin_after_refunds=0., reviews=0, low_reviews=0))
    by_ledger = {s['_event_id'].partition('settlement:')[2]:s for s in sales.values()}
    for u,s in sales.items():
        if now-s.get('paid_at',s['_ts']) < 22200:continue
        if not all(type(s.get(k)) in (int,float) and math.isfinite(s[k]) and s[k]>=0 for k in ('price','cost')):continue
        g=groups[s['sku']];g['mature_sales']+=1;g['price']+=s['price'];g['cost']+=s['cost'];g['refunds']+=refunds[u]
        g['margin_after_refunds']+=s['price']-s['cost']-refunds[u]
    for (ledger,_),r in reviews.items():
        sale=by_ledger.get(ledger);star=r.get('stars')
        if sale and type(star) in (int,float) and 1<=star<=5:
            g=groups[sale['sku']];g['reviews']+=1;g['low_reviews']+=star<=3
    packet=dict(department='회계·리스크실',at=now,expires_at=now+120,rules_hash=rules,skus=dict(groups),
        limitations=['Sale cohort margin excludes unsold expiry and acquisition-to-sale storage; procurement charges these separately.',
                     'Future refunds and demand shifts remain possible; this is not a profit guarantee.'])
    journal.put(KEY,packet)
    return packet


def procurement_department(candidates, inv, capacity, config, journal, catalog, now):
    from .shop_growth import poisson_tail
    from .meta import rank_progress
    packet=accounting_department(journal,catalog,now)
    feed_at=journal.get('public_feed_caught_up_at',0)
    if not 0<=now-feed_at<180:return []
    held=Counter(u['sku'] for u in inv.get('units',[]) if not u.get('expires_at') or u['expires_at']>now)
    board=journal.snapshot('board.json',{})
    choices=[];reasons=Counter()
    for c in candidates:
        e=c['evidence'];sku=c['sku'];g=packet['skus'].get(sku,{})
        kind=catalog[sku].get('kind');n=g.get('mature_sales',0)
        if kind=='goods' and (n<3 or g.get('margin_after_refunds',0)<=0):
            reasons['unproven_or_negative_goods']+=1;continue
        if g.get('reviews',0)>=5 and g.get('low_reviews',0)/g['reviews']>.1:
            reasons['observed_low_review_cohort']+=1;continue
        cost=e['scenario_unit_cost'];price=e['resale_price_scenario'];hours=e['planning_hours']
        refund=max(e.get('refund_upper_scenario',e.get('refund_stress',0)),g.get('refunds',0)/max(1,g.get('price',0)))
        # Every unsold food unit is charged its full acquisition cost. This is
        # conservative even when it would still be on the terminal book.
        storage=cost*.01*hours/24 if kind=='goods' else 0
        arrivals=max(0,e.get('arrival_rate_scenario',0))*hours
        deltas=[]
        for i in range(1,c['qty']+1):
            prob=poisson_tail(arrivals,held[sku]+i)
            book=prob*(price*(1-refund)-cost)-storage
            if kind=='food':book-=(1-prob)*cost
            if not math.isfinite(book) or book<=0:break
            deltas.append(dict(book=book,revenue=prob*price*(1-refund),profit=book))
        if not deltas:reasons['wait_beats_marginal_purchase']+=1;continue
        delta={k:sum(d[k] for d in deltas) for k in deltas[0]}
        gain=rank_progress(board,'shop','seochanit',delta)
        choices.append((gain if gain is not None else delta['book'],c,deltas))
    selected=[];used=0
    for _,c,deltas in sorted(choices,key=lambda r:(r[0],sum(d['book'] for d in r[2])),reverse=True):
        if len(selected)>=min(10,config['restock_max_lines']):break
        unit=c['capital_commitment']/c['qty'];q=min(len(deltas),int(max(0,capacity['spendable']-used)/unit))
        if q<=0:continue
        row=copy.deepcopy(c);ratio=q/c['qty'];row['qty']=q
        for k in ('capital_commitment','max_cost'):row[k]*=ratio
        row['expected_delta']={k:sum(d[k] for d in deltas[:q]) for k in deltas[0]}
        row['score']=row['expected_delta']['book'];row['evidence']['company_approval']={
            'department':'조달실','accounting_at':packet['at'],'rules_hash':packet['rules_hash'],
            'wait_delta':0,'marginal_book':[d['book'] for d in deltas[:q]],'cohort':packet['skus'].get(c['sku'],{})}
        selected.append(row);used+=row['capital_commitment']
    journal.put('company-procurement:v30',dict(department='조달실',at=now,expires_at=now+120,
        rules_hash=packet['rules_hash'],rejections=dict(reasons),buy=[dict(sku=r['sku'],qty=r['qty']) for r in selected],
        forecast_book=sum(r['expected_delta']['book'] for r in selected),capital=used))
    return selected


def buyer_score_department(journal, current, ahead, config, now=None):
    now=time.time() if now is None else now
    from .score_campaign import enabled, context
    if enabled(config):
        packet=context(journal,now)
        if packet.get('bid_targets'):
            return packet['bid_targets']
    if not ahead:return [min(1,current.get('saved',0)+config.get('buyer_saved_target_margin',.002))]
    n=sum(0<=now-r['_ts']<3600 for r in journal.events('purchase',2000))
    if not config.get('market_end_at'):return [min(1,current['saved']+config.get('buyer_saved_target_margin',.002))]
    end=datetime.fromisoformat(config['market_end_at']).timestamp()
    future=max(1,n*max(0,end-now)/3600)
    target=(min(ahead)*(current['deals']+100+future)-current['saved']*(current['deals']+100))/future
    return sorted({min(1,max(0,target)),min(1,current['saved']+config.get('buyer_saved_target_margin',.002))})
