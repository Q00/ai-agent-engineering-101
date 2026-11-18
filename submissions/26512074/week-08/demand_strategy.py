"""Public-flow observation and a single demand/portfolio procurement policy."""
import json
import math
import re
import statistics
import time
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import urlopen
from strategy_policy import database,read,load,category,portfolio,reconcile


def fetch_market_sample(path):
    requested=time.time()
    with urlopen('https://market.ourolabs.ai'+path,timeout=6) as response:
        data=json.load(response);headers=getattr(response,'headers',{})
        sample=dict(requested_at=requested,response_at=time.time(),server_date=headers.get('Date'),cache_age=headers.get('Age'))
    return data,sample


def sync_owned_shop_urls(md,board,now):
    """Use official owner identity; a rotated tunnel is still our same shop."""
    from optimizer import atomic
    path=Path(md)/'shop-identity.json';identity=read(path,{})
    verified={s.get('card',{}).get('url') for s in board.get('shops',[]) if (s.get('owner') or {}).get('login')=='totato2203'}-{None,''}
    urls=set(identity.get('urls',[]))|verified
    if urls!=set(identity.get('urls',[])):
        identity.update(urls=sorted(urls),updated_at=now,source='official_board_owner_login')
        atomic(path,identity)
    return verified


def grade_targets(total,mass):
    """Integer demand allocation; tiny minority shares do not each get a unit."""
    if not mass:return {}
    weight=sum(mass.values())
    if weight<=0:mass={g:1 for g in mass};weight=len(mass)
    raw={g:total*v/max(.001,weight) for g,v in mass.items()}
    result={g:math.floor(v) for g,v in raw.items()}
    remainder=total-sum(result.values())
    for g in sorted(raw,key=lambda g:(raw[g]-result[g],mass[g]),reverse=True)[:remainder]:result[g]+=1
    return result


def hydrate_unfilled_grades(md,now):
    """Read only our rejected tasks, cache constraints without private budgets."""
    with database(md) as c:
        pending=[r['task'] for r in c.execute("select distinct u.task from unfilled_requests u left join meta m on m.key='unfilled_grade:'||u.task where u.at>? and m.key is null order by u.at desc limit 20",(now-load(md)['lookback_seconds'],))]
    if not pending:return
    from urllib.request import Request
    def lookup(task):
        body=json.dumps({'jsonrpc':'2.0','id':task,'method':'GetTask','params':{'id':task}}).encode()
        try:
            request=Request('http://127.0.0.1:1100/',data=body,headers={'Content-Type':'application/json','A2A-Version':'1.0'})
            with urlopen(request,timeout=1.5) as response:result=json.load(response)
            if result.get('error'):return None
            messages=(result.get('result') or {}).get('history',[])
            text=next((' '.join(p.get('text','') for p in m.get('parts',[])) for m in messages if m.get('role')=='ROLE_USER'),'')
            grades=sorted({g.upper() for g in re.findall(r'\bgrade\s*[:=]?\s*([ABC])\b',text,re.I)})
            return 'unfilled_grade:'+task,json.dumps(grades)
        except (OSError,ValueError):return None
    with ThreadPoolExecutor(max_workers=4) as pool:found=[r for r in pool.map(lookup,pending) if r]
    if found:
        with database(md,True) as c:c.executemany('insert or replace into meta values(?,?)',found)


def quantile(values,fraction=.5):
    values=sorted(values)
    return values[min(len(values)-1,max(0,int((len(values)-1)*fraction)))] if values else None


def record_order(md,order,now=None):
    """Only actual local acceptable/use constraints; never private spending authority."""
    now=time.time() if now is None else now
    if not order.get('order') or not order.get('acceptable'):return
    allowed={k:order[k] for k in ('order','item','category','kind','acceptable','use_within_hours','expires') if k in order}
    with database(md,True) as c:
        c.execute('insert or ignore into orders values(?,?,?)',(order['order'],now,json.dumps(allowed,ensure_ascii=False)))


def observe(md,inventory,catalog,events,shops,now,coverage=None):
    from optimizer import atomic
    from strategy_policy import warranty_rate
    from shop_profit import ownership_tags
    md=Path(md);p=load(md);tags=ownership_tags();cutoff=now-p['lookback_seconds']
    with database(md,True) as c:
        c.executemany('insert or ignore into market_events values(?,?,?,?)',
                      [(e['seq'],e['ts'],e['kind'],json.dumps(e,ensure_ascii=False)) for e in events
                       if e.get('seq') is not None and e.get('round',3)==3])
        c.executemany('insert or ignore into shop_history values(?,?,?)',
                      [(s['name'],now,float(s['alive']) if s.get('alive') is not None else .75) for s in shops])
        rows=[json.loads(r[0]) for r in c.execute('select payload from market_events where ts>? and ts<=? order by ts,seq',(now-86400,now))]
        uptimes={r[0]:r[1] for r in c.execute('select name,avg(alive) from shop_history where at>? group by name',(now-3600,))}
        orders=[json.loads(r[0]) for r in c.execute('select payload from orders where at>?',(now-86400,))]
        quote_flow={r['sku']:dict(offers=r['offers'],paid=r['paid'] or 0) for r in c.execute('select sku,count(*) offers,sum(settled_at is not null) paid from price_quotes where inquiry=0 and at>? group by sku',(cutoff,))}
        quote_grades=defaultdict(dict)
        for r in c.execute('select sku,grade,count(*) offers,sum(settled_at is not null) paid from price_quotes where inquiry=0 and at>? group by sku,grade',(cutoff,)):
            quote_grades[r['sku']][r['grade']]=dict(offers=r['offers'],paid=r['paid'] or 0)
        unfilled={r['sku']:r['n'] for r in c.execute('select sku,count(*) n from unfilled_requests where at>? group by sku',(cutoff,))}
        unfilled_grades=defaultdict(Counter)
        for r in c.execute("select u.sku,m.value from unfilled_requests u join meta m on m.key='unfilled_grade:'||u.task where u.at>?",(cutoff,)):
            grades=json.loads(r['value'])
            if len(grades)==1:unfilled_grades[r['sku']][grades[0]]+=1
        reconcile(c,inventory,rows,tags,now)
    groups=defaultdict(list);supplies=defaultdict(list);refunds=defaultdict(float);sales={}
    cost_by_unit={u['unit']:u['cost'] for u in inventory.get('units',[])}
    own_supplies=0;own_storage=0;own_expiry=0;own_refunds=0;own_refund_units={}
    for e in rows:
        d=e.get('data',{});sku=d.get('sku') or d.get('item');kind=e['kind']
        if kind=='settle' and sku in catalog and d.get('amount',0)>0:
            sales[d.get('unit') or d.get('order')]=e
            if e['ts']>=cutoff:groups[sku].append(e)
        elif kind=='supply' and sku in catalog and e['ts']>=cutoff and d.get('qty',0)>0:
            supplies[sku].append(d['total']/d['qty'])
        elif kind in ('broken','refund'):
            refunds[d.get('unit') or d.get('order')]=max(refunds[d.get('unit') or d.get('order')],d.get('refunded',0) if kind=='broken' else d.get('amount',0))
        if d.get('shop') in tags:
            if kind=='supply':own_supplies+=d.get('total',0)
            if kind=='storage':own_storage+=d.get('fee',0)
            if kind=='expire':own_expiry+=d.get('cost',0)
            if kind in ('broken','refund'):
                identity=d.get('unit') or d.get('order') or e['seq']
                own_refund_units[identity]=max(own_refund_units.get(identity,0),d.get('refunded',0) if kind=='broken' else d.get('amount',0))
    own_refunds=sum(own_refund_units.values())
    # Recover exact own acquisition costs, never allocate a batch total as an
    # invented per-unit observation.
    path=md/'optimization.sqlite3'
    supplied_by_unit={}
    if path.exists():
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(path,timeout=5)) as c:
            cost_by_unit.update({r[0]:r[1] for r in c.execute('select unit,cost from units') if r[1] is not None})
            supplied_by_unit={r[0]:r[1] for r in c.execute('select unit,supplied from units')}
    competitive=defaultdict(float);competitor_names=defaultdict(set)
    for shop in shops:
        if shop.get('name') in tags or shop.get('url') in tags:continue
        # A single offline sample never erases competing inventory.
        availability=max(.25,uptimes.get(shop['name'],.75))
        for sku,qty in shop.get('stock',{}).items():
            if qty>0:competitive[sku]+=qty*availability;competitor_names[sku].add(shop['name'])
    result={};risk_rates={};seller_patterns={}
    goods_grades={u['unit']:u.get('grade') for u in inventory.get('units',[]) if u.get('kind')=='goods'}
    for e in sales.values():
        d=e['data'];sku=d.get('sku') or d.get('item')
        if catalog.get(sku,{}).get('kind')=='goods':goods_grades[d.get('unit')]=d.get('grade')
    acquired=Counter()
    for e in rows:
        d=e['data']
        if e['kind']=='supply' and d.get('shop') in tags and catalog.get(d.get('sku'),{}).get('kind')=='goods':
            acquired.update(goods_grades[u] for u in d.get('units',[]) if goods_grades.get(u) in ('A','B','C'))
    grade_weights={g:acquired[g]/sum(acquired.values()) for g in ('A','B','C')} if acquired else dict(A=1/3,B=1/3,C=1/3)
    hours=p['lookback_seconds']/3600
    for sku,product in catalog.items():
        trades=groups.get(sku,[]);own=[e for e in trades if e['data'].get('shop') in tags]
        by_grade=defaultdict(list);base_grade=defaultdict(list);own_base=defaultdict(list);fresh=[];lives=[]
        for e in trades:
            d=e['data'];g=str(d.get('grade') or 'unknown');by_grade[g].append(d['amount'])
            hours_w=d.get('warranty_hours') or 0
            key=(sku,g,hours_w)
            if key not in risk_rates:risk_rates[key]=warranty_rate(md,sku,g,hours_w,p)
            base_grade[g].append(d['amount']*(1-risk_rates[key]))
            if d.get('shop') in tags:own_base[g].append(d['amount']*(1-risk_rates[key]))
            profile=seller_patterns.setdefault(d.get('shop'),dict(deals=0,revenue=0,goods=0,food=0,warranted=0))
            profile['deals']+=1;profile['revenue']+=d['amount'];profile[product.get('kind','goods')]+=1;profile['warranted']+=int(hours_w>0)
            life=(d.get('expires_at')-e['ts'])/3600 if isinstance(d.get('expires_at'),(int,float)) else None
            if life is not None:lives.append(life)
            if product.get('kind')!='food' or life is not None and life>=min(6,product.get('shelf_hours') or 24):fresh.append(d['amount'])
        result[sku]=dict(trades=len(trades),buyers=len({e['data'].get('buyer') for e in trades if e['data'].get('buyer')}),
                         own_trades=len(own),market_rate=len(trades)/hours,own_rate=len(own)/hours,
                         recent_trades=sum(e['ts']>=now-1800 for e in trades),recent_rate=sum(e['ts']>=now-1800 for e in trades)*2,
                         own_recent_trades=sum(e['ts']>=now-1800 for e in own),own_recent_rate=sum(e['ts']>=now-1800 for e in own)*2,
                         own_quotes=quote_flow.get(sku,{}).get('offers',0),own_quote_paid=quote_flow.get(sku,{}).get('paid',0),
                         unfilled_requests=unfilled.get(sku,0),
                         unfilled_by_grade=dict(unfilled_grades[sku]),own_quote_grades=quote_grades[sku],
                         prices={g:dict(n=len(v),q35=quantile(v,.35),median=quantile(v),q65=quantile(v,.65),
                                        base_median=quantile(base_grade[g]),warranty_normalized=True) for g,v in by_grade.items()},
                         own_prices={g:dict(n=len(v),base_median=quantile(v)) for g,v in own_base.items()},
                         fresh_price=quantile(fresh,.35),fresh_samples=len(fresh),remaining_hours_median=quantile(lives),
                         supply_cost=quantile(supplies[sku],.65),supply_cost_mean=sum(supplies[sku])/len(supplies[sku]) if supplies[sku] else None,supply_samples=len(supplies[sku]),
                         competitor_stock=competitive[sku],competitors=len(competitor_names[sku]))
        recoveries=[max(0,e['ts']-supplied_by_unit[e['data']['unit']])/3600 for e in own if e['data'].get('unit') in supplied_by_unit]
        result[sku]['actual_recovery']=dict(n=len(recoveries),median_hours=statistics.median(recoveries) if recoveries else None)
    if p.get('marginal_demand_allocation_enabled'):
        from capital_policy import stock_demand_posteriors
        posteriors=stock_demand_posteriors(inventory,catalog,rows,result,supplied_by_unit,now,hours)
        for sku,posterior in posteriors.items():result[sku]['demand_posterior']=posterior
        from capital_policy import focus_inventory_audit
        focus_inventory_audit(md,inventory,result,catalog,rows,supplied_by_unit,cost_by_unit,now)
    warranties=defaultdict(lambda:dict(mature=0,refunded=0,revenue=0,refund_amount=0,hours_total=0))
    for identity,e in sales.items():
        d=e['data'];hours_w=d.get('warranty_hours') or 0
        if not hours_w or e['ts']+min(6,hours_w)*3600+300>now:continue
        r=warranties[f"{d.get('sku') or d.get('item')}:{d.get('grade')}"]
        r['mature']+=1;r['refunded']+=int(refunds[identity]>0);r['revenue']+=d['amount'];r['refund_amount']+=refunds[identity]
        r['hours_total']+=min(6,hours_w)
    for r in warranties.values():
        r['refund_rate']=max(r['refunded']/r['mature'],r['refund_amount']/max(1,r['revenue']))
        r['mean_hours']=r['hours_total']/r['mature']
    ours=[e for e in sales.values() if e['data'].get('shop') in tags]
    known=[e for e in ours if e['data'].get('unit') in cost_by_unit]
    gross=sum(e['data']['amount']-cost_by_unit[e['data']['unit']] for e in known)
    stock=sum(u['cost'] for u in inventory.get('units',[]));cash_profit=inventory.get('balance',0)-inventory.get('start',5000000)-inventory.get('injected',0)
    summary=dict(at=now,version=p['version'],latest_event=max((e['ts'] for e in rows),default=0),coverage=coverage or {},
                 skus=result,warranties=dict(warranties),actual_order_observations=orders,
                 acquisition_grade_weights=grade_weights,acquisition_grade_samples=sum(acquired.values()),
                 seller_patterns=sorted([dict(shop=k,**v) for k,v in seller_patterns.items()],key=lambda r:-r['revenue'])[:20],
                 financial=dict(window_seconds=86400,cash=inventory.get('balance'),cash_profit=cash_profit,book=cash_profit+stock,
                                stock_cost=stock,revenue=sum(e['data']['amount'] for e in ours),supply_spend=own_supplies,
                                storage=own_storage,expiry=own_expiry,refunds=own_refunds,known_cost_sales=len(known),
                                settled_sales=len(ours),known_realized_gross=gross,
                                partial_realized_net=gross-own_storage-own_expiry-own_refunds,
                                economic_profit_complete=len(known)==len(ours)))
    with database(md,True) as c:c.execute('insert or replace into observations values(?,?)',(now,json.dumps(dict(at=now,financial=summary['financial'],coverage=summary['coverage']))))
    atomic(md/'strategy-market.json',summary)
    from patch36 import observe_buyer_market
    observe_buyer_market(md,rows,catalog,now)
    if p.get('buyer_time_policy_enabled'):
        from round3_metrics import update as update_runtime_metrics
        update_runtime_metrics(md,rows,catalog,now)
    if p.get('inventory_velocity_enabled'):
        from capital_policy import inventory_review
        inventory_review(md,inventory,catalog,summary,now)
    return summary


def collect(md,force=False):
    """Latest-tail recovery prevents an old cursor from hiding current flow."""
    from optimizer import atomic
    from shop_profit import ownership_tags
    md=Path(md);p=load(md);now=time.time();last=read(md/'strategy-collection.json')
    if not force and 0<=now-last.get('attempted_at',0)<p['refresh_seconds']:return read(md/'strategy-market.json')
    with database(md,True) as c:
        lease=c.execute("select value from meta where key='collection_lease'").fetchone()
        if lease and 0<=now-float(lease[0])<p['refresh_seconds'] and not force:return read(md/'strategy-market.json')
        c.execute("insert or replace into meta values('collection_lease',?)",(str(now),))
        boot=c.execute('select max(seq) from market_events').fetchone()[0]
    atomic(md/'strategy-collection.json',dict(attempted_at=now,status='collecting'))
    if boot is None:
        from ledger_window import window
        local=window(md/'ledger.jsonl',now,ownership_tags())
        with database(md,True) as c:
            c.executemany('insert or ignore into market_events values(?,?,?,?)',
                          [(e['seq'],e['ts'],e['kind'],json.dumps(e,ensure_ascii=False)) for e in local])
            boot=max((e['seq'] for e in local),default=0)
    samples={}
    def fetch(path):
        data,samples[path]=fetch_market_sample(path)
        return data
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            a=pool.submit(fetch,'/api/ledger?after=-500');b=pool.submit(fetch,'/api/shops');d=pool.submit(fetch,'/api/board?round=3')
            tail=a.result();shops=b.result()['shops'];board=d.result()
        sync_owned_shop_urls(md,board,samples['/api/board?round=3']['response_at'])
        from score_coordination import snapshot_targets
        board_at=samples['/api/board?round=3']['response_at']
        atomic(md/'official-board-observation.json',samples['/api/board?round=3'])
        snapshot_targets(md,board,board_at)
        from patch36 import snapshot as buyer_score_snapshot
        buyer_score_snapshot(md,board,board_at)
        if p.get('terminal_supply_evidence_enabled'):
            from capital_policy import ROUND_END
            interval=60 if ROUND_END-board_at<120 else 300
            archive=Path(__file__).resolve().parent/'logs/final3h-monitor'/f'{int(board_at//interval)*interval}.json'
            if not archive.exists():
                inv=read(md/'inventory.json')
                atomic(archive,dict(at=board_at,version=p['version'],board=board,cash=inv.get('balance'),
                    stock_cost=sum(u.get('cost',0) for u in inv.get('units',[])),worker='existing_collector_no_new_daemon'))
        cursor=boot or 0;gap=False;events=[]
        if tail and cursor<tail[0]['seq']-5000:
            gap=True;cursor=tail[0]['seq']-1
        for _ in range(10):
            if not tail or cursor>=tail[0]['seq']-1:break
            page=fetch('/api/ledger?after='+str(cursor))
            if not page:break
            events.extend(page);cursor=page[-1]['seq']
        if tail and cursor<tail[0]['seq']-1:gap=True
        events={e['seq']:e for e in events+tail};events=list(sorted(events.values(),key=lambda e:e['seq']))
        # Keep the existing audit journal for checkout/receipt recovery. Only
        # genuinely new sequence IDs from this collector are appended.
        with database(md) as c:seen={r[0] for r in c.execute('select seq from market_events where seq>=?',(events[0]['seq'] if events else 0,))}
        new=[e for e in events if e['seq'] not in seen]
        if new:
            with (md/'ledger.jsonl').open('a',encoding='utf-8') as out:
                for e in new:out.write(json.dumps(e,ensure_ascii=False)+'\n')
        alive={s['name']:s.get('alive') for s in board['shops']}
        for s in shops:s['alive']=alive.get(s['name'])
        ours=[s for s in board['shops'] if (s.get('owner') or {}).get('login')=='totato2203']
        now=time.time()
        hydrate_unfilled_grades(md,now)
        summary=observe(md,read(md/'inventory.json'),read(md/'catalog.json'),events,shops,now,
                        dict(cursor=max((e['seq'] for e in events),default=cursor),gap_detected=gap,rate_semantics='observed settled lower bound',source='public ledger + shop stock + board alive',
                             own_shop_urls=[s.get('card',{}).get('url') for s in ours if s.get('card',{}).get('url')],own_shop_names=[s['name'] for s in ours]))
        atomic(md/'strategy-collection.json',dict(attempted_at=now,completed_at=time.time(),status='ok',new_events=len(new),gap_detected=gap))
        from strategy_feedback import reconcile_paid,evaluate
        reconcile_paid(md,now);evaluate(md,now)
        if p.get('prepared_procurement_enabled'):
            import copy
            forecast_inv=copy.deepcopy(read(md/'inventory.json'))
            forecast_inv['restock']=dict(open=True,opens_at=now,closes_at=now+180)
            plan(md,forecast_inv,read(md/'catalog.json'),now,dry_run=True)
            prepared=read(md/'procurement-preview.json');prepared['prepared_only']=True
            atomic(md/'procurement-prepared.json',prepared)
        return summary
    except Exception as exc:
        atomic(md/'strategy-collection.json',dict(attempted_at=now,status='error',error=str(exc)[:300]))
        return read(md/'strategy-market.json')


def substitution(sku,product,market,catalog,policy,index=None):
    """Cross-SKU demand is explicitly an estimate, not public order volume."""
    value=product['list_price'];actual=index['orders'].get(sku,[]) if index else [o for o in market.get('actual_order_observations',[]) if sku in o.get('acceptable',[])]
    related={s for o in actual for s in o['acceptable'] if s!=sku}
    related={s for s in related if s in catalog and category(catalog[s])==category(product)
             and value/policy['price_band_factor']<=catalog[s].get('list_price',0)<=value*policy['price_band_factor']}
    source='observed_acceptable_estimate' if related else 'category_price_estimate'
    if not related:
        members=index['categories'].get(category(product),[]) if index else catalog
        related={s for s in members if s!=sku and category(catalog[s])==category(product)
                 and value/policy['price_band_factor']<=catalog[s].get('list_price',0)<=value*policy['price_band_factor']}
    rate=sum(market['skus'].get(s,{}).get('market_rate',0) for s in related)/max(1,len(related))
    return rate*policy['substitute_weight'],source,actual


def plan(md,inventory,catalog,now=None,dry_run=False):
    from optimizer import atomic
    from shop_profit import ownership_tags
    from profit_strategy import surge_factor
    md=Path(md);now=time.time() if now is None else now;p=load(md);market=read(md/'strategy-market.json')
    if p.get('prepared_procurement_enabled') and not dry_run:
        from capital_policy import prepared_orders
        prepared=prepared_orders(md,inventory,catalog,now,p)
        if prepared is not None:return prepared
    if not inventory.get('restock',{}).get('open'):
        return dict(buy=[])
    age=now-market.get('at',0)
    if not market or age>p['max_market_age_seconds'] or now-market.get('latest_event',0)>p['max_market_age_seconds']:
        atomic(md/'procurement-preview.json',dict(at=now,strategy=p['version'],reason='stale_market_observation',buy=[],candidates=[],excluded=[]))
        return dict(buy=[])
    horizon=max(60,inventory['restock'].get('opens_at',now)+p['supply_cycle_seconds']-now)/3600
    held=Counter(u['sku'] for u in inventory.get('units',[]) if u.get('grade')!='expired' and (u.get('expires_at') or math.inf)>now)
    index=dict(categories=defaultdict(list),orders=defaultdict(list))
    for sku,product in catalog.items():index['categories'][category(product)].append(sku)
    for o in market.get('actual_order_observations',[]):
        for sku in o.get('acceptable',[]):index['orders'][sku].append(o)
    total_market=sum(v.get('trades',0) for v in market['skus'].values())
    total_own=sum(v.get('own_trades',0) for v in market['skus'].values())
    total_offers=sum(v.get('own_quotes',0) for v in market['skus'].values())
    total_paid=sum(v.get('own_quote_paid',0) for v in market['skus'].values())
    observed_conversion=total_paid/max(1,total_offers)
    held_by_grade=Counter((u.get('sku'),u.get('grade')) for u in inventory.get('units',[]))
    observed_share=total_own/max(1,total_market) if total_own else 1/50
    weights=market.get('acquisition_grade_weights',dict(A=1/3,B=1/3,C=1/3))
    service_slots=min(10,max(1,int((inventory['restock'].get('closes_at',now+180)-now-20)/12)))
    active_skus=sum(v.get('own_trades',0)>0 or v.get('own_quote_paid',0)>0 for v in market['skus'].values())
    # Cover the time needed to revisit selling SKUs through limited supply
    # calls, rather than financing a fixed twelve-hour pile in every market.
    if p.get('inventory_velocity_enabled'):
        from capital_policy import replenish_hours
        service_hours=replenish_hours(inventory,now,p['supply_cycle_seconds'])
    else:service_hours=p['supply_cycle_seconds']/3600*max(1,active_skus/service_slots)
    candidates=[];excluded=[];current_pending=Counter();pending_cash=0
    if p.get('marginal_demand_allocation_enabled'):
        with database(md) as connection:
            for lock in connection.execute('select sku,qty,amount from funds where until>?',(now,)):
                current_pending[lock['sku']]+=lock['qty'];pending_cash+=lock['amount']
    for sku,product in catalog.items():
        if p.get('procurement_exclusions',{}).get(sku,{}).get('until',0)>now:
            excluded.append(dict(sku=sku,reason='mature_loss_review_pause'));continue
        m=market['skus'].get(sku,{});value=product.get('list_price',0)
        if value<=0:continue
        sub,source,orders=substitution(sku,product,market,catalog,p,index)
        rate=m.get('market_rate',0);own=m.get('own_rate',0)
        recent=m.get('recent_trades',0);own_recent=m.get('own_recent_trades',0)
        forecast_rate=(m.get('recent_rate',rate)*recent+rate*4)/(recent+4)
        own_forecast=(m.get('own_recent_rate',own)*own_recent+own*4)/(own_recent+4)
        if m.get('trades',0)<p['min_trades'] or m.get('buyers',0)<p['min_buyers']:
            if sub<=0:continue
            # Alternatives without exact trade/price evidence use a price-band
            # prior only when comparable settlements and supply support entry.
        food=product.get('kind')=='food';prices=[(v.get('base_median',v['median']),v['n']) for v in m.get('prices',{}).values() if v.get('n',0)>0]
        expected=m.get('fresh_price') if food else sum(price*n for price,n in prices)/sum(n for _,n in prices) if prices else None
        estimated=False
        if expected is None:
            peers=[s for s in index['categories'][category(product)] if value/2<=catalog[s].get('list_price',0)<=value*2]
            ratios=[v['q35']/catalog[s]['list_price'] for s in peers for v in market['skus'].get(s,{}).get('prices',{}).values() if v.get('n',0)>=p['min_trades']]
            if len(ratios)<3:continue
            expected=value*quantile(ratios,.25);estimated=True
        offers=m.get('own_quotes',0);paid=m.get('own_quote_paid',0)
        inquiry_rate=(offers/max(.01,p['lookback_seconds']/3600))*paid/max(1,offers) if offers else 0
        hourly=max(own_forecast,inquiry_rate,forecast_rate*observed_share)
        unmet_rate=m.get('unfilled_requests',0)/max(.01,p['lookback_seconds']/3600)*(paid+4*observed_conversion)/(offers+4)
        hourly=max(hourly,own_forecast+unmet_rate)
        proven=m.get('own_trades',0)>=3 or own_recent>=2
        life=product.get('shelf_hours') or 24
        if food:
            eligible=[o for o in orders if (o.get('use_within_hours') or 0)<=life]
            if orders:hourly*=len(eligible)/len(orders)
        if hourly<=0:continue
        cover=min(service_hours,life/4) if food else service_hours
        if not proven:cover=min(cover,p['supply_cycle_seconds']/3600)
        confirmed_buffer=math.ceil(min(unmet_rate*cover,math.sqrt(max(0,hourly*cover)))) if unmet_rate>0 and p.get('inventory_velocity_enabled') else 0
        target=math.ceil(hourly*cover)+confirmed_buffer
        effective_held=held[sku]
        target_grades={}
        if food:
            required=quantile([o.get('use_within_hours') or 0 for o in orders if (o.get('use_within_hours') or 0)<=life]) or 0
            eligible_units=[u for u in inventory.get('units',[]) if u['sku']==sku and u.get('grade')!='expired' and
                            (u.get('expires_at') or now)-now>=max(required,cover)*3600]
            effective_held=len(eligible_units)
            # Food grades decay. Only enforce grade cover when actual rejected
            # requests named one grade; otherwise do not invent strict demand.
            if any(m.get('unfilled_by_grade',{}).values()):
                conversion=(paid+4*observed_conversion)/(offers+4)
                mass={g:m.get('own_prices',{}).get(g,{}).get('n',0)+m.get('prices',{}).get(g,{}).get('n',0)*observed_share+1/3+m.get('unfilled_by_grade',{}).get(g,0)*conversion for g in ('A','B','C')}
                target_grades=grade_targets(target,mass)
                eligible_grades=Counter(u.get('grade') for u in eligible_units)
                effective_held=sum(min(eligible_grades[g],n) for g,n in target_grades.items())
        else:
            conversion=(paid+4*observed_conversion)/(offers+4)
            demand_mass={g:m.get('own_prices',{}).get(g,{}).get('n',0)+m.get('prices',{}).get(g,{}).get('n',0)*observed_share+w+m.get('unfilled_by_grade',{}).get(g,0)*conversion for g,w in weights.items()}
            target_grades=grade_targets(target,demand_mass)
            effective_held=sum(min(held_by_grade[sku,g],n) for g,n in target_grades.items())
        need=max(0,target-effective_held)
        if p.get('marginal_demand_allocation_enabled'):need=max(0,need-current_pending[sku])
        if not need:continue
        need=min(20,need)  # Official supply request maximum, not a portfolio cap.
        surge=surge_factor(inventory,sku,need);worst=math.ceil(value*.75*surge)
        base_cost=m.get('supply_cost_mean') if p.get('terminal_slot_allocation_enabled') else m.get('supply_cost')
        if base_cost is None:base_cost=m.get('supply_cost')
        if base_cost is None:
            ratios=[market['skus'][s]['supply_cost']/catalog[s]['list_price'] for s in index['categories'][category(product)]
                    if value/2<=catalog[s].get('list_price',0)<=value*2 and market['skus'].get(s,{}).get('supply_samples',0)>=3 and market['skus'][s].get('supply_cost')]
            if len(ratios)<3:excluded.append(dict(sku=sku,reason='no_supply_cost_evidence'));continue
            base_cost=value*quantile(ratios,.65)
        # Public costs already include current surge; do not multiply twice.
        cost=min(worst,base_cost*max(1,surge/max(1,surge_factor(inventory,sku,1))))
        if not food:
            # Procurement receives random grades. Never value the entire batch
            # at the A-heavy mix buyers happened to purchase.
            def realizable(g):
                public=m.get('prices',{}).get(g,{})
                reference=public.get('base_median',public.get('median',cost))
                actual=m.get('own_prices',{}).get(g,{})
                n=actual.get('n',0)
                return (actual.get('base_median',reference)*n+reference*2)/(n+2)
            expected=sum(w*realizable(g) for g,w in weights.items())
        # New stock joins the existing queue. A one-unit wait understated both
        # storage and capital lock-up when this SKU already held many units.
        wait=(effective_held+(need+1)/2)/max(hourly,.001)
        if not food:
            held_grades={g:held_by_grade[sku,g] for g in weights}
            mass=sum(demand_mass.values())
            grade_wait={g:(held_grades[g]+(need*w+1)/2)/max(.001,hourly*demand_mass[g]/max(.001,mass)) for g,w in weights.items() if w>0}
            wait=sum(weights[g]*v for g,v in grade_wait.items())
        survival=min(1,hourly*life/max(1,effective_held+need)) if food else 1
        storage=0 if food else cost*inventory.get('storage_per_day',.01)*wait/24
        # Replacement stock is sold without optional warranty unless requested;
        # warranted selling floors/fees are computed by the same funding module.
        net=expected*survival-cost-storage
        marginal_fields={}
        if p.get('marginal_demand_allocation_enabled'):
            from capital_policy import cover_satisfied,marginal_curve
            from patch36 import cost_estimate
            posterior=m.get('demand_posterior',{})
            if not posterior:
                excluded.append(dict(sku=sku,reason='awaiting_stock_exposure_posterior'));continue
            acq={'*':1.} if food else weights
            queue={'*':effective_held} if food else {g:held_by_grade[sku,g] for g in acq}
            raw_cost={g:cost if food else cost_estimate(value,sku,g,md) for g in acq}
            scale=cost/max(1,sum(acq[g]*raw_cost[g] for g in acq))
            marginal_fields=dict(demand_posterior=posterior,acquisition_weights=acq,held_by_grade=queue,
                grade_costs={g:raw_cost[g]*scale for g in acq},grade_expected_sale={'*':expected} if food else {g:realizable(g) for g in acq},
                next_supply_hours=service_hours,shelf_hours=life,existing_pending_qty=current_pending[sku])
            marginal_fields['actual_recovery']=m.get('actual_recovery',{})
            temporary=dict(marginal_fields,kind=product.get('kind'),estimated_cost=cost,expected_sale=expected)
            if cover_satisfied(temporary,queue,current_pending[sku]):
                excluded.append(dict(sku=sku,reason='existing_and_pending_stock_covers_next_supply',held_by_grade=queue));continue
            curve=marginal_curve(temporary,inventory,now,need,current_pending[sku])
            positive=0
            for unit in curve:
                if unit['book']<=0:break
                if p.get('terminal_supply_evidence_enabled'):
                    from capital_policy import terminal_purchase_eligible
                    if not terminal_purchase_eligible(dict(temporary,actual_recovery=marginal_fields['actual_recovery']),unit,now):break
                positive+=1
            if not positive:
                reason='terminal_recovery_not_supported_before_close' if p.get('terminal_supply_evidence_enabled') and curve and curve[0]['book']>0 else 'nonpositive_marginal_book_after_queue_storage_expiry'
                excluded.append(dict(sku=sku,reason=reason,first_unit=curve[0] if curve else None));continue
            need=positive;net=curve[0]['book'];marginal_fields['marginal_values']=curve[:positive]
        loss_exception=False
        if net<=0 and p.get('inventory_velocity_enabled'):
            from score_coordination import boundary_value,all_value
            targets_snapshot=read(md/'score-targets.json',{})
            from capital_policy import remaining_hours
            probability=min(1,hourly*remaining_hours(now)/max(1,effective_held+need))
            deltas=dict(book=net*need,revenue=expected*need*probability)
            discrete=boundary_value(targets_snapshot,'shop',deltas,now);smooth=all_value(targets_snapshot,'shop',deltas,now)
            loss_exception=bool(proven and discrete is not None and discrete>0 and smooth is not None and smooth>0 and probability>=.95)
        if (net<=0 and not loss_exception) or net>0 and net/max(1,cost)<p['min_roi']:
            excluded.append(dict(sku=sku,reason='non_positive_risk_adjusted_margin',net=net));continue
        candidates.append(dict(sku=sku,category=category(product),kind=product.get('kind'),need=need,
                               **marginal_fields,
                               target=target,held=held[sku],effective_held=effective_held,hourly=hourly,market_rate=rate,own_rate=own,
                               service_cover_hours=cover,
                               grade_targets=target_grades,service_hours=cover,required_hours=required if food else 0,verified_stockout_buffer=confirmed_buffer,loss_exception=loss_exception,
                               unfilled_demand_rate=unmet_rate,grade_wait_hours=grade_wait if not food else {},
                               forecast_market_rate=forecast_rate,forecast_own_rate=own_forecast,
                               momentum=forecast_rate/max(.001,rate),grade_mix_estimated=not food,
                               buyers=m.get('buyers',0),competitor_stock=m.get('competitor_stock',0),
                               estimated_cost=cost,worst_cost=worst,surge=surge,expected_sale=expected,expected_net=net,
                               roi=net/cost,contribution=net*min(need,hourly*cover),rotation=net/cost/max(.05,wait),payback_hours=wait,
                               profit_per_hour=net*min(need,hourly*cover)/max(.01,cover),proven_demand=proven,observed_market_share=observed_share,
                               substitution_source=source,substitute_rate_estimate=sub,price_estimated=estimated))
    from score_coordination import procurement_value
    targets=read(md/'score-targets.json',{})
    for v in candidates:v['rank_contribution']=procurement_value(v,targets,now)
    max_profit=max((v['rank_contribution'] for v in candidates),default=1);max_rotation=max((v['rotation'] for v in candidates),default=1)
    for v in candidates:v['score']=p['contribution_weight']*v['rank_contribution']/max_profit+p['rotation_weight']*v['rotation']/max_rotation
    deadline=inventory['restock'].get('closes_at',now+180)
    slots=min(10,max(0,int((deadline-now-20)/12)))
    # Both cash and supply-call slots are scarce. Pure ROI sorting can waste
    # all ten calls on tiny orders while a large profitable batch is available.
    available=max(1,inventory.get('balance',0)-(pending_cash if p.get('marginal_demand_allocation_enabled') else 0))
    for v in candidates:
        affordable=min(v['need'],math.floor(available/v['worst_cost']))
        flow=v['expected_net']*affordable/max(.05,v['payback_hours'])
        capacity=max(1/max(1,slots),affordable*v['worst_cost']/available)
        from score_coordination import all_value
        modeled=all_value(targets,'shop',dict(book=v['expected_net']*affordable,revenue=v['expected_sale']*affordable),now)
        v['modeled_all_progress']=modeled
        v['funding_objective']='class_all_progress_per_capacity_hour' if modeled is not None else 'net_profit_fallback_missing_scoreboard'
        v['capital_overhang_ratio']=max(1,v['held']/max(1,v['target'])) if p.get('inventory_velocity_enabled') else 1
        v['funding_priority']=(modeled/max(.05,v['payback_hours']) if modeled is not None else flow)/capacity/v['capital_overhang_ratio'] if affordable else 0
        if p.get('terminal_slot_allocation_enabled'):
            from capital_policy import terminal_funding
            v.update(terminal_funding(v,inventory,targets,now,slots))
    if p.get('marginal_demand_allocation_enabled'):
        from capital_policy import cash_shadow_price
        shadow=cash_shadow_price(candidates,available,slots)
        for v in candidates:
            tax=shadow*sum(u['cash_locked_to_next_window'] for u in v.get('marginal_values',[])[:min(v['need'],int(available/max(1,v['worst_cost'])))])
            capacity=max(1/max(1,slots),min(v['need'],int(available/max(1,v['worst_cost'])))*v['worst_cost']/available)
            v['cash_opportunity_all']=tax;v['cash_shadow_all_per_credit']=shadow
            if v.get('modeled_all_progress') is not None:v['funding_priority']=(v['modeled_all_progress']-tax)/capacity
        excluded.extend(dict(sku=v['sku'],reason='next_window_opportunity_exceeds_purchase',cash_opportunity_all=v['cash_opportunity_all']) for v in candidates if v['funding_priority']<=0)
        candidates=[v for v in candidates if v['funding_priority']>0]
    ranked=sorted(candidates,key=lambda v:(-v['funding_priority'],-v['profit_per_hour'],-v['rotation']))
    if p.get('terminal_slot_allocation_enabled'):
        excluded.extend(dict(sku=v['sku'],reason='negative_finite_round_book_contribution') for v in ranked if v['terminal_book_per_unit']<=0 and not v.get('loss_exception'))
        ranked=[v for v in ranked if v['terminal_book_per_unit']>0 or v.get('loss_exception')]
    buy=[]
    with database(md,True) as c:
        rows=[json.loads(r[0]) for r in c.execute('select payload from market_events where ts>?',(now-3600,))]
        reconcile(c,inventory,rows,ownership_tags(),now)
        locks=[dict(r) for r in c.execute('select * from funds where until>?',(now,))]
        limits=portfolio(inventory,catalog,read(md/'warranty-holds.json'),locks,now,p,md)
        cash=limits['budget'];sku_exposure=dict(limits['sku_exposure']);cat_exposure=dict(limits['category_exposure'])
        pending_qty=Counter()
        for r in locks:pending_qty[r['sku']]+=r['qty']
        projected_targets=targets
        for v in ranked:
            if len(buy)>=slots:break
            room=min(cash,limits['sku_cap']-sku_exposure.get(v['sku'],0),limits['category_cap']-cat_exposure.get(v['category'],0))
            desired=v['need'] if p.get('marginal_demand_allocation_enabled') else max(0,v['need']-pending_qty[v['sku']])
            qty=min(desired,max(0,math.floor(room/v['worst_cost'])))
            if not qty:excluded.append(dict(sku=v['sku'],reason='shared_funding_or_existing_cover'));continue
            if v.get('loss_exception'):
                from score_coordination import boundary_value
                gain=boundary_value(projected_targets,'shop',dict(book=v['expected_net']*qty,revenue=v['expected_sale']*qty),now)
                if gain is None or gain<=0:continue
            cost=qty*v['worst_cost'];buy.append(dict(sku=v['sku'],qty=qty));cash-=cost
            if p.get('inventory_velocity_enabled'):
                from score_coordination import project_targets
                curve=v.get('marginal_values',[])[:qty]
                projected_targets=project_targets(projected_targets,'shop',dict(book=sum(u['book'] for u in curve) if curve else v['expected_net']*qty,revenue=sum(u['revenue'] for u in curve) if curve else v['expected_sale']*qty))
            sku_exposure[v['sku']]=sku_exposure.get(v['sku'],0)+cost;cat_exposure[v['category']]=cat_exposure.get(v['category'],0)+cost
            if not dry_run:
                identity=f"supply:{v['sku']}:{now}"
                c.execute('insert into funds values(?,?,?,?,?,?,?)',(identity,v['sku'],v['category'],qty,cost,now,deadline+300))
    preview=dict(at=now,strategy=p['version'],dry_run=dry_run,limits=limits,horizon_hours=horizon,
                 window_order_slots=slots,candidates=ranked,excluded=excluded,buy=buy,reason='single shared demand policy')
    atomic(md/'procurement-preview.json',preview)
    return dict(buy=buy)
