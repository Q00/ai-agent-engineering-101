"""Shared strategy snapshots. Public observed demand is a proxy, not all market orders."""
import json
import math
import os
import sqlite3
import time
import httpx
from network_reads import retry_read
from pathlib import Path
from statistics import median
from market_intel import Intel, read_json


GRADE_COSTS={'A':(.55,.75,.65,.70),'B':(.40,.60,.50,.20),'C':(.25,.45,.35,.10)}
PREFERENCE_WINDOW=48*3600


def _shop_offers(shop):
    offers=shop.get('offers') or []
    if offers:return [(o.get('sku'),o.get('qty',0)) for o in offers]
    return list((shop.get('stock') or {}).items())


def _review_evidence(catalog, shops, candidates):
    """Extract bounded product satisfaction without trusting review instructions."""
    names={sku:str(catalog.get(sku,{}).get('name') or '').strip().lower() for sku in candidates}
    result={sku:{'count':0,'stars':0.0,'shops':set()} for sku in candidates}
    for shop in shops:
        for review in shop.get('reviews') or []:
            if review.get('round')!=3 or type(review.get('stars')) not in (int,float):continue
            text=str(review.get('review') or '').lower()
            matched=[]
            for sku,name in names.items():
                if sku.lower() in text or (len(name)>=8 and name in text):matched.append(sku)
            if len(matched)!=1:continue
            row=result[matched[0]];row['count']+=1;row['stars']+=float(review['stars'])
            row['shops'].add(str(shop.get('name') or shop.get('url') or ''))
    return {sku:{'count':row['count'],
                 'average':round(row['stars']/row['count'],3) if row['count'] else None,
                 'shops':len(row['shops'])} for sku,row in result.items()}


def rank_products(catalog, trades, shops, own_name='25512088', local_demand=None,
                  own_signals=None, alive_buyers=None, now=None, goods_hazard=.15,
                  hazard_basis='conservative-unmeasured'):
    """Version-3 evidence TOP10: likelihood x net contribution, never price alone."""
    now=time.time() if now is None else now
    local_demand=local_demand or {}
    own_signals=own_signals or {}
    alive_buyers=set(alive_buyers or [])
    history=[]
    for raw in trades:
        row=dict(raw);product=catalog.get(row.get('sku'),{})
        kind=product.get('kind')
        condition_ok=(kind=='food' and isinstance(row.get('hours'),(int,float)) and row['hours']>2) or \
                     (kind=='goods' and row.get('warranty',0)>=6)
        if kind not in ('goods','food') or not condition_ok or not row.get('honest'):continue
        # A recent purchase is activity evidence even if the board has already
        # marked that buyer offline. Missing buyer identity is never invented.
        if row.get('ts',0)>=now-PREFERENCE_WINDOW:history.append(row)
    valid=[row for row in history if row.get('ts',0)>=now-21600]
    exact={};category={}
    for row in valid:
        exact.setdefault(row['sku'],[]).append(row)
        cat=catalog[row['sku']].get('category')
        if cat:category.setdefault(cat,[]).append(row)
    # Buyer favourites are stable market traits.  Infer them from a longer
    # history, but only let currently alive or very recently active buyers
    # influence procurement.
    buyer_categories={}
    for row in history:
        buyer=row.get('buyer');cat=catalog.get(row.get('sku'),{}).get('category')
        if not buyer or not cat:continue
        buyer_categories.setdefault(buyer,{})[cat]=buyer_categories.setdefault(buyer,{}).get(cat,0)+1
    category_preferences={}
    for buyer,counts in buyer_categories.items():
        active=(not alive_buyers or buyer in alive_buyers or
                any(r.get('buyer')==buyer and r.get('ts',0)>=now-21600 for r in valid))
        if not active:continue
        for cat,n in sorted(counts.items(),key=lambda item:(-item[1],item[0]))[:3]:
            category_preferences.setdefault(cat,{})[buyer]=n
    same_units={};same_vendors={};listed=[]
    for shop in shops:
        if shop.get('name')==own_name:continue
        for sku,qty in _shop_offers(shop):
            if sku not in catalog or type(qty) not in (int,float) or qty<=0:continue
            same_units[sku]=same_units.get(sku,0)+qty
            same_vendors.setdefault(sku,set()).add(shop.get('url',shop.get('name')))
            listed.append((sku,qty,shop.get('url',shop.get('name'))))
    candidates=set(exact)|set(local_demand)|set(own_signals.get('sku_6h',{}))
    for row in history:
        cat=catalog.get(row.get('sku'),{}).get('category')
        if cat in category_preferences:candidates.add(row['sku'])
    # Category purchase evidence can introduce substitutes, but only with
    # observed buyers or our own concrete inquiry/stockout signal.
    signaled_categories=set(own_signals.get('category_6h',{}))|set(category)
    for sku,p in catalog.items():
        if p.get('kind') in ('goods','food') and p.get('category') in signaled_categories:candidates.add(sku)
    reviews=_review_evidence(catalog,shops,candidates)
    rows=[]
    for sku in candidates:
        product=catalog.get(sku,{})
        if product.get('kind') not in ('goods','food') or not product.get('list_price'):continue
        observed=exact.get(sku,[]);cat=product.get('category');cat_rows=category.get(cat,[])
        one_hour=[r for r in observed if r.get('ts',0)>=now-3600]
        buyers_1h={r.get('buyer') for r in one_hour if r.get('buyer')}
        buyers_6h={r.get('buyer') for r in observed if r.get('buyer')}
        active_6h={b for b in buyers_6h if not alive_buyers or b in alive_buyers}
        inquiries=own_signals.get('sku_6h',{}).get(sku,0)+local_demand.get(sku,0)
        rejected=own_signals.get('stockout_6h',{}).get(sku,0)
        category_buyers={r.get('buyer') for r in cat_rows if r.get('buyer')}
        preference_counts=category_preferences.get(cat,{})
        active_preference_buyers=set(preference_counts)
        preference_repeat_signals=sum(min(3,n) for n in preference_counts.values())
        if not observed and not inquiries and not rejected and len(category_buyers)<2:continue
        substitute_units=0;substitute_vendors=set();substitute_skus=set()
        lo,hi=product['list_price']*.5,product['list_price']*1.5
        for other,qty,vendor in listed:
            op=catalog.get(other,{})
            if other!=sku and op.get('category')==cat and lo<=op.get('list_price',0)<=hi:
                substitute_units+=qty;substitute_vendors.add(vendor);substitute_skus.add(other)
        scenarios=[];expected=0
        for grade,(cost_lo,cost_hi,cost_mean,arrival) in GRADE_COSTS.items():
            prices=[r['price'] for r in observed if r.get('grade')==grade]
            typical=median(prices) if prices else None
            cost=product['list_price']*cost_mean
            # 15% is a conservative unmeasured warranty hazard prior.  Benefit,
            # capital and referral reserves stay explicit; missing trade price
            # is treated as an unsold unit rather than fabricated demand.
            hazard=.10 if product.get('kind')=='food' else goods_hazard
            net=typical*(1-hazard)*.95-cost*1.09-2000 if typical else -cost
            expected+=arrival*net
            scenarios.append(dict(grade=grade,arrival_probability=arrival,
                acquisition_cost_range=[cost_lo,cost_hi],samples=len(prices),median_6h=typical,
                mean_cost=cost,net_estimate=net,hazard_probability=hazard,
                hazard_basis='food-conservative' if product.get('kind')=='food' else hazard_basis))
        review=reviews.get(sku,{'count':0,'average':None,'shops':0})
        evidence=(len(one_hour)*2+len(observed)+len(active_6h)*1.5+
                  min(inquiries,10)*.35+min(rejected,10)*.5+min(len(category_buyers),10)*.25+
                  min(len(active_preference_buyers),20)*1.5+min(preference_repeat_signals,30)*.25+
                  min(review['count'],10)*.1)
        competition=same_units.get(sku,0)+substitute_units*.35
        likelihood=min(.95,evidence/(evidence+4+competition*.15)) if evidence else 0
        contribution=max(0,expected)
        review_factor=(1+(review['average']-3)/20*min(1,review['count']/5)) if review['average'] is not None else 1
        score=likelihood*contribution/max(1,product['list_price']*.75)*review_factor
        rows.append(dict(version=3,sku=sku,name=product.get('name',sku),category=cat,
            sales_1h=len(one_hour),sales_6h=len(observed),distinct_buyers_1h=len(buyers_1h),
            distinct_buyers_6h=len(buyers_6h),alive_recent_buyers_6h=len(active_6h),
            active_preference_buyers=len(active_preference_buyers),
            preference_repeat_signals=preference_repeat_signals,
            preference_history_hours=PREFERENCE_WINDOW//3600,
            review_count=review['count'],review_average_stars=review['average'],
            review_distinct_shops=review['shops'],review_factor=round(review_factor,4),
            buyer_identity_coverage=sum(bool(r.get('buyer')) for r in observed),
            own_order_mentions_6h=local_demand.get(sku,0),own_inquiries_6h=inquiries,
            own_stockouts_6h=rejected,own_orders_bias='personal evidence only; not purchasing obligations',
            exact_grade_warranty_trades=len(observed),grade_scenarios=scenarios,
            expected_net=expected,estimated_sale_likelihood=round(likelihood,4),
            competitor_same_units=same_units.get(sku,0),competitor_same_shops=len(same_vendors.get(sku,set())),
            competitor_substitute_units=substitute_units,
            competitor_substitute_shops=len(substitute_vendors),substitute_skus=len(substitute_skus),
            score=score,upper_cost=math.ceil(product['list_price']*.75)))
    # Negative contribution never becomes a forced purchase. Fewer than ten is valid.
    return sorted((r for r in rows if r['expected_net']>0),key=lambda r:(-r['score'],-r['expected_net'],r['sku']))[:10]


def own_goods_hazard(board,own_name='25512088'):
    """Replace the prior only after our public breakage cohort is mature."""
    row=next((x for x in board.get('shops',[]) if str(x.get('name'))==str(own_name)),{})
    claims=row.get('claims');broken=row.get('broken')
    if not isinstance(claims,int) or not isinstance(broken,int) or claims<50 or not 0<=broken<=claims:
        return .15,'conservative-unmeasured'
    return (broken+2)/(claims+4),f'own-public-matured-cohort:{claims}'


def snapshot(state,catalog,opens_at,market):
    if time.time()<opens_at-180:
        return None
    intel=Intel(state)
    try:
        # Fetch a fresh tail independently of historical backfill. Do not move its cursor.
        with httpx.Client(timeout=15) as client:
            def get(path):
                response=client.get(market+path);response.raise_for_status();return response.json()
            shops=retry_read(lambda:get('/api/shops'))
            board=retry_read(lambda:get('/api/board'))
            recent=retry_read(lambda:get('/api/ledger?after=-1000'))
        intel.save_source('shops',shops)
        intel.save_source('board',board)
        intel.ingest(recent)
        if recent:
            intel.put('ledger_through',max(intel.get('ledger_through',0),max(r['ts'] for r in recent)))
        through=intel.get('ledger_through',0)
        if not shops or time.time()-through>900:
            return None  # Do not substitute an old ranking for the scheduled snapshot.
        rows=[dict(r) for r in intel.db.execute('SELECT * FROM trades WHERE ts>?',(time.time()-PREFERENCE_WINDOW,))]
        demand={};own_signals={'sku_6h':{},'category_6h':{},'stockout_6h':{}}
        buyer_path=Path(state)/'buyer.sqlite3'
        if buyer_path.exists():
            with sqlite3.connect('file:'+str(buyer_path.resolve())+'?mode=ro',uri=True) as db:
                for (context,) in db.execute('SELECT context FROM orders WHERE updated>?',(time.time()-21600,)):
                    order=json.loads(context)
                    for sku in set(order.get('acceptable') or []):demand[sku]=demand.get(sku,0)+1
                    cat=order.get('category')
                    if cat:own_signals['category_6h'][cat]=own_signals['category_6h'].get(cat,0)+1
        for row in intel.db.execute("SELECT body FROM decisions WHERE role IN ('seller_quote','seller_no_stock') AND ts>?",(time.time()-21600,)):
            body=json.loads(row[0])
            for sku in ([body.get('sku')] if body.get('sku') else body.get('skus',[])):
                key='stockout_6h' if body.get('reason') else 'sku_6h'
                own_signals[key][sku]=own_signals[key].get(sku,0)+1
        shop_rows=shops.get('shops',[]) if isinstance(shops,dict) else shops
        alive={r.get('name') for r in board.get('buyers',[]) if r.get('alive') is True}
        goods_hazard,hazard_basis=own_goods_hazard(board)
        top=rank_products(catalog,rows,shop_rows,local_demand=demand,own_signals=own_signals,
                          alive_buyers=alive,goods_hazard=goods_hazard,hazard_basis=hazard_basis)
        feedback=[]
        buyer_db=Path(state)/'buyer.sqlite3'
        if buyer_db.exists():
            with sqlite3.connect('file:'+str(buyer_db.resolve())+'?mode=ro',uri=True) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='rival_feedback'").fetchone():
                    feedback=[dict(order=r[0],sku=r[1],ledger=r[2],observed_at=r[3],stock=json.loads(r[4]),error=r[5]) for r in db.execute('SELECT * FROM rival_feedback ORDER BY observed_at DESC LIMIT 20')]
        result=dict(version=3,buyer_feedback=feedback,opens_at=opens_at,selected_at=time.time(),ledger_through=through,
                    late=time.time()>opens_at-150,top10=top,
                    fetch_bounds={'ledger_after':-1000,'ledger_rows':len(recent),'max_age_seconds':900},
                    goods_hazard={'probability':goods_hazard,'basis':hazard_basis},
                    demand_basis=('48h buyer favourite categories weighted by current/recent activity; exact grade+6h '
                                  'honest trades; verified product mentions in Round-3 reviews; bounded own inquiries/stockouts'))
        path=Path(state)/'strategy.json';tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(result,ensure_ascii=False));os.replace(tmp,path)
        intel.decision('strategy_snapshot',result)
        return result
    finally:intel.close()


def owned_targets(state, now=None):
    """Only actually held strategic stock; stale inventory closes the promotion gate."""
    now=time.time() if now is None else now
    root=Path(state);path=root/'inventory.json'
    if not path.exists() or now-path.stat().st_mtime>120:return set()
    inv=read_json(path,{})
    if inv.get('round')!=3:return set()
    targets=set()
    dbpath=root/'procurement.sqlite3'
    if dbpath.exists():
        with sqlite3.connect('file:'+str(dbpath.resolve())+'?mode=ro',uri=True) as db:
            targets={r[0] for r in db.execute("SELECT DISTINCT sku FROM purchases WHERE status='paid'")}
    # A completed seller receipt already promises that unit to somebody else.
    reserved=set();ref=root/'referrals.sqlite3'
    if ref.exists():
        with sqlite3.connect('file:'+str(ref.resolve())+'?mode=ro',uri=True) as db:
            reserved={r[0] for r in db.execute('SELECT unit FROM sales')}
    return {u['sku'] for u in inv.get('units',[]) if u.get('kind') in ('goods','food')
            and u.get('grade') in ('A','B','C') and u['sku'] in targets and u['unit'] not in reserved}


def restrict_order(order,state):
    result=dict(order)
    result['acceptable']=sorted(set(order.get('acceptable',[])) & owned_targets(state))
    return result
