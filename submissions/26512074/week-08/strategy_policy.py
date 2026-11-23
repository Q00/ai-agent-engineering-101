"""One configurable portfolio and one cross-process funding ledger. No model calls."""
import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parent
_risk_cache={}
_market_cache={}


def market_snapshot(md):
    path=Path(md)/'strategy-market.json'
    try:stamp=path.stat().st_mtime_ns
    except FileNotFoundError:return {}
    key=str(path.resolve());cached=_market_cache.get(key)
    if cached is None or cached[0]!=stamp:
        cached=(stamp,read(path));_market_cache[key]=cached
    return cached[1]


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:return {} if default is None else default


def load(md=None):
    path=Path(md)/'strategy-policy.json' if md is not None and (Path(md)/'strategy-policy.json').exists() else ROOT/'strategy-policy.json'
    p=read(path)
    for name in ('cash_reserve_fraction','inventory_fraction','sku_fraction','category_fraction',
                 'substitute_weight','warranty_prior','warranty_safety','price_exploration_fraction'):
        if isinstance(p.get(name),bool) or not isinstance(p.get(name),(float,int)) or not 0<=p[name]<=1:
            raise ValueError('Invalid strategy fraction: '+name)
    for name in ('refresh_seconds','supply_cycle_seconds','lookback_seconds','min_trades','min_buyers',
                 'price_min_opportunities','price_review_seconds','price_maturity_seconds','buyer_quote_parallelism','buyer_request_timeout'):
        if not isinstance(p.get(name),(int,float)) or not math.isfinite(p[name]) or p[name]<=0:raise ValueError('Invalid strategy setting: '+name)
    if not p.get('price_multipliers') or any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in p['price_multipliers']):
        raise ValueError('Invalid price arms')
    if any(isinstance(q,bool) or not isinstance(q,(int,float)) or not math.isfinite(q) or not 0<q<=1 for q in p.get('seller_margin_share_targets',[])):
        raise ValueError('Invalid margin-share targets')
    return p


def active(md):
    md=Path(md)
    return bool((md.resolve()==(ROOT/'.market').resolve() or (md/'strategy-policy.json').exists()) and load(md).get('enabled'))


@contextmanager
def database(md,write=False):
    md=Path(md);md.mkdir(exist_ok=True,parents=True)
    c=sqlite3.connect(md/'strategy.sqlite3',timeout=60)
    c.row_factory=sqlite3.Row
    try:
        c.executescript('''
        create table if not exists market_events(seq integer primary key,ts real,kind text,payload text);
        create index if not exists market_events_time on market_events(ts);
        create index if not exists market_events_shop_time on market_events(json_extract(payload,'$.data.shop'),ts);
        create index if not exists market_events_order_kind on market_events(json_extract(payload,'$.data.order'),kind);
        create table if not exists meta(key text primary key,value text);
        create table if not exists observations(at real primary key,payload text);
        create table if not exists shop_history(name text,at real,alive real,primary key(name,at));
        create table if not exists orders(id text primary key,at real,payload text);
        create table if not exists funds(id text primary key,sku text,category text,qty integer,amount real,at real,until real);
        create table if not exists reconciled_supply(seq integer primary key);
        create table if not exists price_quotes(task text primary key,unit text,sku text,grade text,at real,arm real,
          base real,quote real,cost real,inquiry integer,settle_seq integer unique,settled_at real,paid real,
          refunds real default 0,storage real default 0,warranty real default 0);
        create table if not exists price_state(sku text,grade text,arm real,at real,reason text,primary key(sku,grade));
        create index if not exists price_quotes_unit on price_quotes(unit);
        create index if not exists price_quotes_sku_grade_time on price_quotes(sku,grade,at);
        create table if not exists price_losses(seq integer,task text,amount real,primary key(seq,task));
        create table if not exists unfilled_requests(task text,sku text,at real,primary key(task,sku));
        create table if not exists buyer_predictions(order_id text primary key,at real,payload text);
        create table if not exists official_buyer_snapshots(at real primary key,payload text);
        create table if not exists price_contract_sessions(sku text,grade text,hours real,epoch integer,arm real,unit text,base real,at real,primary key(sku,grade,hours,epoch));
        create table if not exists unit_acquisitions(unit text primary key,sku text,cost real,at real,source text);
        create table if not exists review_events(seq integer primary key,buyer text,stars real,at real,sku text,grade text);
        create table if not exists quote_context(task text primary key,buyer text,confidence text);
        create table if not exists access_audit(task text primary key,at real,pattern text,decision text,confidence text);
        create table if not exists price_diagnostics(sku text,grade text,at real,payload text,primary key(sku,grade));
        create table if not exists price_sessions(sku text,grade text,epoch integer,arm real,unit text,base real,at real,primary key(sku,grade,epoch));
        create table if not exists price_exposures(task text primary key,sku text,grade text,epoch integer,at real,arm real,inquiry integer);
        ''')
        if write:c.execute('begin immediate')
        yield c
        c.commit()
    except BaseException:
        c.rollback();raise
    finally:c.close()


def category(product):return product.get('category') or product.get('kind','unknown')


def warranty_rate(md,sku,grade,hours,policy=None):
    p=policy or load(md);path=Path(md)/'strategy-market.json'
    try:stamp=path.stat().st_mtime_ns
    except FileNotFoundError:stamp=0
    key=str(path.resolve());cached=_risk_cache.get(key)
    if cached is None or cached[0]!=stamp:
        cohorts=read(path).get('warranties',{})
        goods={s for s,v in read(Path(md)/'catalog.json').items() if v.get('kind')=='goods'}
        pools={}
        for identity,v in cohorts.items():
            item,g=identity.rsplit(':',1);n=v.get('mature',0);h=v.get('mean_hours',0)
            if item not in goods or not n or h<=0:continue
            rate=1-(1-min(1,v.get('refund_rate',0)))**(6/min(6,h))
            pool=pools.setdefault(g,[0,0]);pool[0]+=n;pool[1]+=n*rate
        cached=(stamp,cohorts,pools,goods);_risk_cache[key]=cached
    cohort=cached[1].get(f'{sku}:{grade}',{})
    prior=p.get('warranty_grade_priors',{}).get(str(grade),p['warranty_prior'])
    n=cohort.get('mature',0);strength=p.get('warranty_prior_strength',4)
    observed=cohort.get('refund_rate',prior)
    mean_hours=max(.001,cohort.get('mean_hours',6))
    observed_6h=1-(1-min(1,observed))**(6/min(6,mean_hours))
    if sku in cached[3]:
        total,loss=cached[2].get(str(grade),(0,0))
        # Leave this SKU out of its prior. Local evidence remains independent
        # and overrides the grade pool as its own sample grows.
        other_n=max(0,total-n)
        other_loss=max(0,loss-n*observed_6h)
        prior=(other_loss+prior*strength)/max(1,other_n+strength)
    rate=(observed_6h*n+prior*strength)/max(1,n+strength)
    return min(1-1e-9,max(0,1-(1-rate)**(min(6,hours)/6)+p['warranty_safety'])) if hours else 0


def portfolio(inventory,catalog,holds,pending,now,policy,md=None):
    cash=max(0,inventory.get('balance',0));units=inventory.get('units',[])
    stock=sum(max(0,u.get('cost',0)) for u in units);equity=cash+stock
    # Refund promises remain recorded, but never sequester buying capital.
    reserve=warranty=storage=0
    locks=[dict(r) for r in pending if r['until']>now]
    locked=sum(r['amount'] for r in locks)
    by_sku={};by_category={}
    for u in units:
        sku=u['sku'];cat=category(catalog.get(sku,u));by_sku[sku]=by_sku.get(sku,0)+u.get('cost',0)
        by_category[cat]=by_category.get(cat,0)+u.get('cost',0)
    for r in locks:
        by_sku[r['sku']]=by_sku.get(r['sku'],0)+r['amount']
        by_category[r['category']]=by_category.get(r['category'],0)+r['amount']
    return dict(cash=cash,stock_cost=stock,equity=equity,cash_reserve=math.ceil(reserve),
                base_cash_reserve=0,warranty_reserve=0,
                storage_reserve=0,pending=locked,inventory_cap=equity,
                sku_cap=equity,category_cap=equity,
                budget=max(0,math.floor(cash-locked)),
                sku_exposure=by_sku,category_exposure=by_category)


def reconcile(c,inventory,events,tags,now):
    c.execute('delete from funds where until<=?',(now,))
    held={u['unit'] for u in inventory.get('units',[]) if u.get('unit')}
    for e in events:
        d=e.get('data',{})
        if e.get('kind')!='supply' or d.get('shop') not in tags:continue
        if c.execute('select 1 from reconciled_supply where seq=?',(e['seq'],)).fetchone():continue
        # Only release a worst-case cash reservation after refreshed inventory
        # actually contains the supplied units. Until then double-count safely.
        if not d.get('units') or not any(u in held for u in d['units']):continue
        row=c.execute('select id from funds where sku=? and at<=? order by at limit 1',(d.get('sku'),e['ts'])).fetchone()
        if row:
            c.execute('delete from funds where id=?',(row['id'],))
            c.execute('insert or ignore into reconciled_supply values(?)',(e['seq'],))


def warranty_offer(md,unit,price,cash,now,hours):
    from optimizer import atomic
    if unit.get('kind')!='goods' or hours not in (3,6,12,24,48,72):return False
    md=Path(md);p=load(md)
    if p.get('joint_seller_objective_enabled') and unit.get('grade') in ('B','C'):
        risk=warranty_rate(md,unit['sku'],unit.get('grade'),hours,p)
        net=price*(1-risk)-unit['cost']
        if net<0:
            from score_coordination import boundary_value
            gain=boundary_value(read(md/'score-targets.json'),'shop',dict(book=net,revenue=price),now)
            if gain is None or gain<=0:return False
    with database(md,True) as c:
        holds={k:v for k,v in read(md/'warranty-holds.json').items() if v['expires_at']>now}
        existing=holds.get(unit['unit'],{})
        if unit.get('grade') not in p.get('warranty_grades',['A']) and not existing:return False
        proposed=dict(price=price,sku=unit['sku'],grade=unit.get('grade'),hours=hours,
                      expires_at=existing.get('expires_at',now+hours*3600+900),issued=existing.get('issued',False))
        holds[unit['unit']]=proposed
        atomic(md/'warranty-holds.json',holds)
    return True


def release_warranty(md,unit,issued=False,price=None,now=None):
    from optimizer import atomic
    md=Path(md)
    with database(md,True):
        holds=read(md/'warranty-holds.json')
        if unit not in holds:return
        if issued:
            holds[unit]['issued']=True
            if price is not None:holds[unit]['price']=price
        elif not holds[unit].get('issued'):del holds[unit]
        atomic(md/'warranty-holds.json',holds)
