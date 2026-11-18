"""Bounded, model-free pricing observations and six-hour policy control.

Only authenticated settlements count as sales. Quotes and signed receipts do
not count as revenue. Missing acquisition costs remain unknown, never zero.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import time
import uuid

ROOT = Path(__file__).resolve().parent
KST_OFFSET = 9*3600

def schedule_slot(now):
    return int((now+KST_OFFSET)//21600)

def next_review_at(now):
    return (schedule_slot(now)+1)*21600-KST_OFFSET

def review_due(state,now):
    # Migrate existing elapsed-time state without discarding any history.
    return not state.get('last_at') or schedule_slot(now)>schedule_slot(state['last_at'])

# Legacy capital/arm fields remain readable for historical policy snapshots.
# Active opening prices and capital allocation use profit-policy.json.
BOUNDS = {'exploration': (.10,.15), 'counter_edge': (.05,.3),
          'concession': (.1,.5), 'stock_multiplier': (.5,1.5),
          'clearance_hours': (3,6),
          'forum_interval': (1800,7200), 'buyer_history_weight': (.3,.8)}

def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default

def atomic(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=True,indent=2),encoding='utf-8')
    os.replace(tmp,path)

def append(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(data,ensure_ascii=True)+'\n')

def validate(p):
    for k,(lo,hi) in BOUNDS.items():
        v=p[k]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError('invalid policy '+k)
    if p['markup_arms'] != [.05,.10,.15,.25,.35,.50,.65]:
        raise ValueError('unapproved markup arms')
    return p

def config(md=None):
    default=validate(read(ROOT/'adaptive-policy.json',{}))
    try:
        return validate(read(Path(md or ROOT/'.market')/'adaptive-policy.json',default))
    except (ValueError,KeyError):
        return default

@contextmanager
def db(md):
    md=Path(md);md.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(md/'optimization.sqlite3',timeout=10)
    c.row_factory=sqlite3.Row
    c.executescript('''
    create table if not exists units(unit text primary key,sku text,cost real,supplied real);
    create table if not exists trials(task text primary key,sku text,unit text,at real,
      arm real,ask int,cost real,grade text,stock int,kind text,buyer text,
      last_offer real,agreed real,settled_at real,price real,margin real);
    create table if not exists seen(seq integer primary key);
    create table if not exists costs(seq integer primary key,at real,kind text,amount real,known int);
    ''')
    columns={row[1] for row in c.execute('pragma table_info(trials)')}
    for name,definition in [('reference','real'),('inquiry','integer not null default 0'),
                            ('responded','integer not null default 0'),('strategy','text'),
                            ('first_offer','real'),('negotiation_rounds','integer not null default 0')]:
        if name not in columns:
            try:
                c.execute('alter table trials add column '+name+' '+definition)
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc):
                    raise
    try:
        yield c;c.commit()
    finally:
        c.close()

def observe(md, inventory, ledger, now):
    from shop_profit import ownership_tags
    tags=ownership_tags()
    with db(md) as c:
        for u in inventory.get('units',[]):
            c.execute('insert or ignore into units values(?,?,?,?)',
                      (u['unit'],u['sku'],u['cost'],u.get('supplied_at',now)))
        for e in ledger:
            d=e.get('data',{});seq=e.get('seq')
            if e.get('round',3)!=3 or seq is None or d.get('shop') not in tags:
                continue
            if e.get('kind')=='supply' and isinstance(d.get('total'),(int,float)):
                # A single-unit supply proves its exact cost. Multi-unit totals
                # are not silently allocated as if per-unit costs were known.
                if d.get('qty')==1 and len(d.get('units',[]))==1:
                    c.execute('insert or ignore into units values(?,?,?,?)',
                              (d['units'][0],d.get('sku'),d['total'],e['ts']))
                c.execute('insert or replace into costs values(?,?,?,?,?)',
                          (seq,e['ts'],'supply',d['total'],1))
            if c.execute('select 1 from seen where seq=?',(seq,)).fetchone():
                continue
            c.execute('insert into seen values(?)',(seq,))
            if e.get('kind')=='settle':
                unit=d.get('unit');price=d.get('amount')
                if not isinstance(price,(int,float)) or isinstance(price,bool):continue
                row=c.execute('select * from units where unit=?',(unit,)).fetchone()
                trial=c.execute('select task,cost from trials where unit=? and settled_at is null and agreed=? and at<=? order by at desc limit 1',(unit,price,e['ts'])).fetchone()
                cost=row['cost'] if row else trial['cost'] if trial else None
                # Old sales are retained, but never assigned to an unobserved arm.
                if trial:
                    c.execute('update trials set settled_at=?,price=?,margin=?,buyer=? where task=?',
                              (e['ts'],price,None if cost is None else price-cost,d.get('buyer'),trial['task']))
                else:
                    c.execute('insert or ignore into trials(task,sku,unit,at,settled_at,price,cost,margin,buyer) values(?,?,?,?,?,?,?,?,?)',
                              ('ledger:'+str(seq),d.get('sku',d.get('item')),unit,e['ts'],e['ts'],price,cost,
                               None if cost is None else price-cost,d.get('buyer')))
            elif e.get('kind') in ('storage','expire','refund','supply','broken'):
                amount=(d.get('fee') if e['kind']=='storage' else d.get('cost') if e['kind']=='expire'
                        else d.get('total') if e['kind']=='supply' else d.get('refunded') if e['kind']=='broken' else d.get('amount'))
                c.execute('insert or ignore into costs values(?,?,?,?,?)',
                          (seq,e['ts'],'refund' if e['kind']=='broken' else e['kind'],amount,int(isinstance(amount,(int,float)))))
        c.execute('update trials set cost=(select cost from units where units.unit=trials.unit), margin=price-(select cost from units where units.unit=trials.unit) where cost is null and exists(select 1 from units where units.unit=trials.unit)')

def risk_cost(unit,now,inventory):
    # Storage is official; future refund probability is unknown, not a measured cost.
    return (unit['cost']*inventory.get('storage_per_day',.01)*max(0,now-unit.get('supplied_at',now))/86400
            if unit.get('kind')=='goods' else 0)

def choose_quote(md,task,unit,inventory,base,now,rng=None,inquiry=False):
    from strategy_policy import active
    if active(md):
        from strategy_feedback import quote
        return quote(md,task,unit,inventory,base,now,inquiry)
    from profit_strategy import opening,settings
    p=config(md);cost=unit['cost'];stock=sum(u['sku']==unit['sku'] for u in inventory.get('units',[]))
    with db(md) as c:
        from turnover import age_terms,expected_turnover
        age=age_terms(unit,now,expected_turnover(c,unit['sku']))
        old=c.execute('select arm,ask from trials where task=?',(task,)).fetchone()
        if old:return dict(base,ask=old['ask'],floor=min(base['floor'],old['ask']),arm=old['arm'])
        c.execute('create table if not exists counterpart_quotes(task text primary key, peer text)')
        peer=None  # A2A metadata is caller-supplied, not authenticated identity.
        if peer:
            peers=c.execute('select t.* from trials t join counterpart_quotes p on p.task=t.task where p.peer=? and t.inquiry=0 and t.at between ? and ? order by t.at desc limit 12',(peer,now-7*86400,now-300)).fetchall()
            if len(peers)>=3:
                paid=sum(r['settled_at'] is not None for r in peers)
                factor=max(.85,min(1.08,1+(paid/len(peers)-.35)*.20))
                base=dict(base,peer_multiplier=factor)
            c.execute('insert or replace into counterpart_quotes values(?,?)',(task,peer))
        high=unit.get('kind')=='goods' and unit['list_price']>=settings()['pilot_min_list_price']
        rows=[dict(r) for r in c.execute("select * from trials where kind=? and grade is ? and reference>0 and inquiry=0 and strategy in (?, 'premium-offensive-v1') and (?=0 or strategy='premium-offensive-v1' or responded>0 or settled_at is not null) and at between ? and ? order by at desc limit 500",
              (unit.get('kind'),unit.get('grade'),settings()['version'],int(high),now-7*86400,now-300))]
        same=[r for r in rows if r['sku']==unit['sku']]
        if unit.get('kind')=='goods' and unit['list_price']>=settings()['pilot_min_list_price']:
            from premium import policy as premium_policy
            profile=premium_policy(md)
            base=dict(base,opening_multiplier=profile.get('skus',{}).get(unit['sku'],{}).get('opening_multiplier',profile.get('opening_multiplier',1)))
        result=opening(unit,base,same if len(same)>=12 else rows)
        if age['steps']:
            result['ask']=max(math.ceil(result['floor']),math.ceil(result['ask']*age['opening_factor']))
            result['pricing_evidence']['turnover']=age
        elif read(Path(md)/'operating-policy.json',{}).get('experimental_uplift_enabled',True) and unit.get('kind')=='goods' and not inquiry and len(same)>=20 and sum(r['settled_at'] is not None for r in same)/len(same)>=.4 and sum((r['margin'] or 0) for r in same)>0 and int(hashlib.sha256(task.encode()).hexdigest()[:8],16)%10==0:
            result['ask']=math.ceil(result['ask']*1.03)
            result['pricing_evidence']['margin_experiment']=dict(uplift=.03,share=.10)
        if base.get('peer_multiplier'):
            result['ask']=max(math.ceil(result['floor']),math.ceil(result['ask']*base['peer_multiplier']))
        resale=read(Path(md)/'resale-units.json',{}).get('units',{}).get(unit.get('unit'))
        if (resale and resale.get('sku')==unit['sku'] and resale.get('acquisition_cost')==cost
                and any(u.get('unit')==unit.get('unit') for u in inventory.get('units',[]))):
            result.update(ask=max(base['floor'],math.ceil(cost*2)),arm=1.0)
            result['pricing_evidence'].update(strategy='verified-resale-100pct',supported=True,
                                              acquisition_cost=cost,purchase_seq=resale['purchase_seq'])
        if result['pricing_evidence']['strategy']!='verified-resale-100pct' and unit.get('expires_at') is not None and unit['expires_at']-now<p['clearance_hours']*3600:
            result.update(ask=base['ask'],floor=min(base['floor'],base['ask']))
            result['arm']=round(result['ask']/max(1,cost)-1,6)
            result['pricing_evidence']['clearance']=True
        c.execute('insert into trials(task,sku,unit,at,arm,ask,cost,grade,stock,kind,reference,inquiry,strategy) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (task,unit['sku'],unit.get('unit'),now,result['arm'],result['ask'],cost,unit.get('grade'),stock,unit.get('kind'),result['reference'],int(inquiry),result['pricing_evidence']['strategy']))
        append(Path(md)/'optimization/opening-prices.jsonl',{'at':now,'task':task,'sku':unit['sku'],'ask':result['ask'],**result['pricing_evidence'],'inquiry':inquiry})
    return dict(base,**result)

def negotiate(md,task,deal,plan,now):
    from pricing_fastpath import counter_ask
    p=config(md);offer=deal['offer'];floor=plan['floor'];ask=deal['ask'] or plan['ask']
    turns=sum(s.startswith('buyer:') for s in deal['lines'])
    with db(md) as c:
        trial=c.execute('select strategy,at from trials where task=?',(task,)).fetchone()
        from turnover import age_terms,expected_turnover
        age=age_terms(deal['unit'],now,expected_turnover(c,deal['unit']['sku']))
        history=c.execute('select count(*) n,sum(case when settled_at is not null then 1 else 0 end) wins from trials where sku=? and inquiry=0 and reference>0 and at between ? and ?',
                          (deal['unit']['sku'],now-7*86400,now-300)).fetchone()
        c.execute('update trials set last_offer=coalesce(?,last_offer),responded=max(responded,?) where task=?',(offer,int(offer is not None and offer>0),task))
        c.execute('update trials set first_offer=coalesce(first_offer,?),negotiation_rounds=max(negotiation_rounds,?) where task=?',(offer,max(0,turns-1),task))
    clearance=deal['unit'].get('expires_at') is not None and deal['unit']['expires_at']-now<p['clearance_hours']*3600
    # Reserve is independent of the opening anchor: high initial quotes leave
    # room for real negotiation rather than a fixed six-percent discount cap.
    liquid=plan.get('liquidity')
    rollout=read(Path(md)/'operating-policy.json',{})
    modern=rollout.get('expected_profit_pricing',False) and trial is not None and trial['at']>=rollout.get('effective_at',float('inf'))
    if not modern and not clearance and not liquid:
        floor=min(ask,max(floor,math.ceil((plan.get('market_median') or 0)*.80)))
    from profit_strategy import settings
    premium_unit=deal['unit'].get('kind')=='goods' and deal['unit']['list_price']>=settings()['pilot_min_list_price']
    if premium_unit and not liquid and not modern:
        from premium import policy as premium_policy
        profile=premium_policy(md).get('skus',{}).get(deal['unit']['sku'],{})
        margin=max(settings()['premium_min_margin'],profile.get('min_margin',0))
        market_floor=max(settings()['premium_market_floor'],profile.get('market_floor',0))
        if age['steps']:
            margin=min(margin,age['minimum_margin']);market_floor=min(market_floor,age['market_floor'])
        floor=min(ask,max(floor,math.ceil(deal['unit']['cost']*(1+margin)),math.ceil((plan.get('market_median') or 0)*market_floor)))
    if offer is None:return {'action':'ask','price':ask,'type':'unknown'}
    if offer>=ask:return {'action':'accept','type':'fast'}
    concession=.05 if trial and trial['strategy']=='verified-resale-100pct' else p['concession']
    if liquid:concession=liquid['concession']
    if premium_unit and not liquid and not modern:
        concession=min(concession,settings()['premium_concession'])
    previous=deal.get('previous_offer')
    improving=previous is not None and offer>previous
    if improving or age['steps']:concession=max(concession,min(.35,.20+age['steps']*.03))
    from strategy_policy import active as coordinated,load as coordinated_policy
    if coordinated(md):concession=coordinated_policy(md).get('seller_concession',concession)
    if coordinated(md) and coordinated_policy(md).get('inventory_velocity_enabled'):
        inv_group=read(Path(md)/'inventory-plan.json',{}).get('groups',{}).get(f"{deal['unit']['sku']}:{deal['unit'].get('grade')}",{})
        if inv_group:
            target=max(.01,inv_group.get('target',0));pressure=inv_group['qty']/target
            concession=pressure/(1+pressure)
    counter=max(floor,min(ask,math.ceil(ask-(ask-floor)*concession)))
    # Actual prior success, shrunk with four conservative pseudo-observations.
    probability=(2+(history['wins'] or 0))/(4+history['n'])
    if previous is not None and offer>previous:probability=min(.85,probability+.15)
    elif previous is not None and offer==previous:probability*=.65
    buyer_type='quality' if deal.get('warranty_hours') else 'multi_turn' if turns>3 else 'sensitive'
    holding=risk_cost(deal['unit'],now,{})
    if modern:
        holding=deal['unit']['cost']*.01/24 if deal['unit'].get('kind')=='goods' else 0
        if deal.get('warranty_hours'):
            if coordinated(md):
                from strategy_policy import warranty_rate
                risk=warranty_rate(md,deal['unit']['sku'],deal['unit'].get('grade'),deal['warranty_hours'])
            else:
                from warranty_risk import statistics,cohort
                risk=cohort(statistics(md,now),deal['unit']['sku'],deal['unit'].get('grade'),deal['warranty_hours'])['refund_rate']
            holding+=max(offer,counter)*risk
    accept=offer-deal['unit']['cost']-holding
    # Include revenue preference and one more round's capital/latency cost.
    if not modern:accept+=offer*.05
    expected=probability*(counter-deal['unit']['cost']-holding+(0 if modern else counter*.05))-max(1,deal['unit']['cost']*.002)
    if coordinated(md) and coordinated_policy(md).get('joint_seller_objective_enabled'):
        from score_coordination import all_value
        from review_policy import context_value
        targets=read(Path(md)/'score-targets.json',{})
        stars=context_value(md,task,deal['unit'].get('grade'))
        av=all_value(targets,'shop',dict(book=accept,revenue=offer,stars_rank=stars),now)
        cv=all_value(targets,'shop',dict(book=counter-deal['unit']['cost']-holding,revenue=counter,stars_rank=stars),now)
        if av is not None and cv is not None:
            delay=deal.get('response_wait_seconds',1.)
            velocities=targets.get('velocities_per_hour',{})
            forgone=all_value(targets,'shop',dict(book=velocities.get('shop:book',0)*delay/3600,
                 revenue=velocities.get('shop:revenue',0)*delay/3600),now) or 0
            accept,expected=av,probability*cv-max(0,forgone)
    # Sparse data is uncertainty, not permission to accept a first lowball.
    # Make one bounded counter unless the offer is already within 3% of ask.
    if improving and offer>=floor and offer>=ask*(.90 if age['steps'] else .95):
        return {'action':'accept','type':buyer_type,'reason':'improving_offer_near_ask','accept_ev':accept,'counter_ev':expected}
    if (history['n']<12 or premium_unit) and turns<=2 and offer>=floor and offer<ask*.97 and not clearance and not liquid and not improving and not age['steps'] and expected>max(0,accept)*(1+p['counter_edge']):
        return {'action':'ask','price':counter,'type':buyer_type,'reason':'one_counter_before_lowball_acceptance'}
    if offer>=floor and (counter<=offer or expected<=max(0,accept)*(1+p['counter_edge'])):
        return {'action':'accept','type':buyer_type,'accept_ev':accept,'counter_ev':expected,'closing_probability':probability,'floor':floor}
    if modern and previous is not None and offer<=previous and offer<floor and turns>=4:return {'action':'reject','type':'repeated_below_risk_floor','floor':floor}
    if turns>=8 or turns>=6 and not (previous is not None and offer>previous):return {'action':'reject','type':buyer_type,'closing_probability':probability,'floor':floor,'offer':offer}
    return {'action':'ask','price':counter,'type':buyer_type,'accept_ev':accept,'counter_ev':expected,'closing_probability':probability,'floor':floor,'offer':offer}

def agreement(md,task,price):
    with db(md) as c:c.execute('update trials set agreed=? where task=?',(price,task))

def interval_metrics(md,start,end):
    with db(md) as c:
        rows=[dict(r) for r in c.execute('select * from trials where inquiry=0 and (at between ? and ? or settled_at between ? and ?)',(start,end,start,end))]
        costs=[dict(r) for r in c.execute('select * from costs where at between ? and ?',(start,end))]
        turnover=[r[0]/3600 for r in c.execute('select t.settled_at-u.supplied from trials t join units u on u.unit=t.unit where t.inquiry=0 and t.settled_at between ? and ? and t.settled_at>=u.supplied',(start,end))]
    settled=[r for r in rows if r['settled_at'] is not None and start<=r['settled_at']<=end];known=[r for r in settled if r['margin'] is not None]
    skus={}
    for r in rows:
        s=skus.setdefault(r['sku'],{'attempts':0,'settled':0,'known_gross_margin':0,'arms':{}})
        s['attempts']+=int(start<=r['at']<=end);s['settled']+=int(r in settled);s['known_gross_margin']+=(r['margin'] or 0) if r in settled else 0
        a=s['arms'].setdefault(str(r['arm']),{'attempts':0,'settled':0,'margin':0})
        a['attempts']+=int(start<=r['at']<=end);a['settled']+=int(r in settled);a['margin']+=(r['margin'] or 0) if r in settled else 0
    losses=sum(r['amount'] for r in costs if r['known'] and r['kind'] in ('storage','expire','refund'))
    attempts=sum(start<=r['at']<=end and r['arm'] is not None for r in rows)
    cohort=[r for r in rows if start<=r['at']<=end and r['arm'] is not None]
    hours=max(1/3600,(end-start)/3600)
    return {'attempts':attempts,'settled':len(settled),'known_cost_sales':len(known),
            'revenue_per_hour':sum(r['price'] or 0 for r in settled)/hours,
            'known_net_per_hour':(sum(r['margin'] for r in known)-losses)/hours,
            'median_inventory_hours':__import__('statistics').median(turnover) if turnover else None,
            'gross_margin_known':sum(r['margin'] for r in known),'observed_costs':costs,
            'partial_realized_net':sum(r['margin'] for r in known)-losses,
            'economic_profit_complete':len(known)==len(settled) and all(r['known'] for r in costs),
            'conversion':sum(r['settled_at'] is not None for r in cohort)/len(cohort) if cohort else None,'skus':skus}

def delta(current,previous,hours):
    fields=('profit','book','revenue','deals','saved','forum','surplus','posts','refunds')
    return {k: {'delta':current[k]-previous[k],'per_hour':(current[k]-previous[k])/hours}
            for k in fields if isinstance(current.get(k),(int,float)) and isinstance(previous.get(k),(int,float))}

def evaluate(previous,current,policy):
    """At most two bounded knobs, with sample and comparable-window guards."""
    if not previous or current['at']-previous['at']<5.5*3600:return policy,'hold: insufficient interval'
    a=current['local'];b=previous['local']
    if min(a['attempts'],b['attempts'])<30 or min(a['settled'],b['settled'])<10:
        return policy,'hold: insufficient samples'
    old=previous['seller'];new=current['seller']
    if new.get('stars',5)<old.get('stars',5)-.15 or (a['conversion'] or 0)<(b['conversion'] or 0)*.7:
        return policy,'rollback: reputation or conversion'
    old_refunds=old.get('refunds',0);new_refunds=new.get('refunds',0)
    if (new_refunds-old_refunds)/max(1,new.get('deals',0)-old.get('deals',0))>.1:
        return policy,'rollback: refund spike'
    metric='book_per_hour' if current.get('book_per_hour') is not None and previous.get('book_per_hour') is not None else 'cash_profit_per_hour'
    if current.get(metric) is None or previous.get(metric) is None:
        return policy,'hold: insufficient profit windows'
    if current[metric]<previous[metric]-max(100,abs(previous[metric])*.2):
        return policy,'rollback: book profit regression; attribution uncertain'
    if not a.get('economic_profit_complete'):
        return policy,'hold: incomplete realized costs'
    result=dict(policy)
    # Arm observations drive quote choice continuously; only expand sampling after
    # measured profitable, sufficiently populated price experiments.
    proven=sum(1 for s in a['skus'].values() for arm in s['arms'].values() if arm['attempts']>=12 and arm['settled']>=4 and arm['margin']>0)
    if proven>=2 and a['partial_realized_net']>0:
        result['exploration']=min(.15,round(policy['exploration']+.01,3))
    if a['partial_realized_net']>0 and a['conversion']>=b['conversion']:
        result['stock_multiplier']=min(1.5,round(policy['stock_multiplier']+.1,2))
    return validate(result),'apply' if result!=policy else 'hold: no justified change'

def review(md,board,shops,now=None,force=False):
    now=time.time() if now is None else now;md=Path(md);out=md/'optimization';out.mkdir(exist_ok=True)
    pending=read(out/'pending-policy.json',None)
    if pending:
        atomic(md/'adaptive-policy.json',validate(pending['policy']))
        atomic(out/'latest.json',pending['snapshot'])
        atomic(out/'state.json',pending['state'])
        (out/'pending-policy.json').unlink()
        return {'status':'recovered_committed_cycle','at':pending['snapshot']['at']}
    state=read(out/'state.json',{})
    if not force and not review_due(state,now):return {'status':'not_due','last_at':state.get('last_at'),'next_at':next_review_at(now)}
    from shop_profit import REGISTRATION,exposure
    inv=read(md/'inventory.json',{});ledger=[]
    # Persisted ledger may contain a partially appended final row.
    with (md/'ledger.jsonl').open(encoding='utf-8') as f:
        for line in f:
            try:ledger.append(json.loads(line))
            except ValueError:pass
    observe(md,inv,ledger,now)
    from premium import review as premium_review
    premium_result=premium_review(md,board,shops,ledger,now)
    try:
        from market_leaders import review as leaders_review
        if Path(md).resolve()==(ROOT/'.market').resolve():
            leaders_review(board,shops)
            from sku_evidence import update as sku_review
            sku_review(md,now)
    except (OSError,ValueError,KeyError) as exc:
        append(Path(md)/'optimization/leader-review-errors.jsonl',{'at':now,'error':str(exc)})
    ranked=sorted([s for s in board['shops'] if not s.get('house') and isinstance(s.get('all'),(int,float))],key=lambda s:-s['all'])
    seller=next((s for s in ranked if s['name']==REGISTRATION),None)
    buyer=next((s for s in board.get('buyers',[]) if s['name']==REGISTRATION),{})
    if not seller:raise ValueError('own shop missing; preserve policy')
    previous=read(out/'latest.json',None)
    elapsed=(now-previous['at'])/3600 if previous else None
    sd=delta(seller,previous['seller'],elapsed) if previous else {}
    public={s['name']:s for s in shops.get('shops',[])}
    from round3 import lines
    observed_aliases={s['name']:{s['name'],s.get('url')} for s in shops.get('shops',[])}
    for quote in lines(md/'recommendation-quotes.jsonl'):
        if quote.get('shop') in observed_aliases:
            observed_aliases[quote['shop']].add(quote.get('url'))
    top=[]
    for rank,s in enumerate(ranked[:5],1):
        old=next((x for x in (previous or {}).get('top5',[]) if x['name']==s['name']),{})
        change=delta(s,old,elapsed) if old else {}
        url=public.get(s['name'],{}).get('url') or (s.get('card') or {}).get('url')
        aliases=observed_aliases.get(s['name'],set())|{s['name'],url}
        settlements=list({e['seq']:e for e in ledger if e.get('kind')=='settle' and e.get('round')==3 and e.get('data',{}).get('shop') in aliases and 0<=now-e.get('ts',0)<21600}.values())
        prices={}
        conditions={}
        for e in settlements:
            d=e['data'];prices.setdefault(d.get('sku',d.get('item')),[]).append(d.get('amount'))
            key=f"{d.get('sku',d.get('item'))}:{d.get('grade')}:{d.get('warranty_hours') or 0}h"
            conditions.setdefault(key,[]).append(d['amount'])
        prices={sku:{'count':len(v),'mean_price':sum(v)/len(v)} for sku,v in prices.items() if v and all(isinstance(x,(int,float)) for x in v)}
        top.append({**{k:s.get(k) for k in ('name','all','profit','revenue','deals','stars','reviews','refunds','honest_claims','claims')},
                    'rank':rank,'delta':change,'stock':public.get(s['name'],{}).get('stock'),
                    'settled_prices_6h':prices,'sku_grade_warranty_6h':{key:{'count':len(values),'median':__import__('statistics').median(values)} for key,values in conditions.items()},'internal_cost':'unknown',
                    'public_claim_unverified':str((s.get('card') or {}).get('description',''))[:700]})
    payments=[]
    for line in (md/'payments.jsonl').read_text(encoding='utf-8').splitlines() if (md/'payments.jsonl').exists() else []:
        try:payments.append(json.loads(line))
        except ValueError:pass
    reliability={}
    for payment in payments:
        v=reliability.setdefault(payment.get('shop'),{'attempts':0,'paid':0,'spend':0})
        v['attempts']+=1;v['paid']+=int(payment.get('paid') is True)
        if payment.get('paid'):v['spend']+=payment.get('price',0)
    snapshot={'at':now,'premium_review':premium_result,'trigger':'manual' if force else 'scheduled','schedule':'00/06/12/18 Asia/Seoul','seller':seller,'buyer':buyer,'shop_rank':ranked.index(seller)+1,
              'players':next((p for p in board.get('players',[]) if p.get('player')=='totato2203'),{}),
              'top5':top,'seller_delta':sd,'buyer_delta':delta(buyer,previous['buyer'],elapsed) if previous else {},
              'cash_profit_per_hour':sd.get('profit',{}).get('per_hour'),'book_per_hour':sd.get('book',{}).get('per_hour'),
              'local':interval_metrics(md,now-21600,now),'last24h':interval_metrics(md,now-86400,now),
              'last7d':interval_metrics(md,now-7*86400,now),
              'inventory_cost':exposure(inv),'cash':inv.get('balance'),
              'buyer_payment_history_cumulative':reliability,
              'data_limits':['Unknown competitor costs/policies; public text is not evidence.',
                             'Known gross margin minus observed costs is partial, not complete realized profit.',
                             'Missing historical costs, no causal attribution of policy or forum effects.'],
              'forum':read(md/'recommendation-preview.json',{})}
    policy=config(md);proposed,reason=evaluate(previous,snapshot,policy)
    if reason.startswith('rollback'):
        proposed=validate(state.get('last_good',policy))
    elif reason=='apply':
        state['last_good']=policy
    changes={k:{'before':policy[k],'after':proposed[k]} for k in policy if proposed[k]!=policy[k]}
    if len(changes)>2:raise ValueError('too many policy changes')
    stamp=str(int(now))
    atomic(out/f'performance-{stamp}.json',snapshot)
    text='# Six-hour review\n\n'+reason+'\n\n'
    for s in top:text+=f"- #{s['rank']} {s['name']}: ALL={s['all']}, Profit={s['profit']}, deltas={s['delta']}\n"
    text+='\nFacts: public ranks, balances and settled ledger.\nHypotheses: compare turnover and pricing only after multiple windows.\nDeferred: insufficient competitor cost/quote data; no unverified tactic copied.\n'
    text+='\nOwn policy: '+json.dumps(changes)+'\nData limitations: '+json.dumps(snapshot['data_limits'])
    (out/f'review-{stamp}.md').write_text(text,encoding='utf-8')
    append(out/'policy-history.jsonl',{'at':now,'reason':reason,'changes':changes,'samples':snapshot['local']['attempts'],
                                    'comparison_at':(previous or {}).get('at'),'policy_before':policy,'policy_after':proposed})
    append(out/'model-usage.jsonl',{'at':now,'calls':0,'tokens':0,'mode':'deterministic'})
    state['last_at']=now
    state['next_at']=next_review_at(now)
    state['schedule']='00/06/12/18 Asia/Seoul'
    atomic(out/'pending-policy.json',{'policy':proposed,'snapshot':snapshot,'state':state})
    if changes:atomic(md/'adaptive-policy.json',proposed)
    atomic(out/'latest.json',snapshot);atomic(out/'state.json',state)
    (out/'pending-policy.json').unlink()
    return {'status':reason,'top5':[s['name'] for s in top],'changes':changes,'at':now,'next_at':state['next_at']}

@contextmanager
def lock(md):
    path=Path(md)/'optimization.lock';path.parent.mkdir(exist_ok=True)
    with path.open('a+b') as f:
        if os.fstat(f.fileno()).st_size==0:f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield

def main():
    print(json.dumps({'status':'six_hour_optimizer_removed',
                      'reason':'Repeated partial policy writes without completed evaluation; retired by owner request.'}))

if __name__=='__main__':main()
