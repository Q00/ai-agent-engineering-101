"""Read-only shop board trends, unit flow, cash capacity and quality evidence.

Baseline/public cutoffs are explicit; no registration, policy, trading or model calls.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
import re
import sqlite3
import sys
import time
from r3.state import ROOT,read_json,policy
from autonomous_status import ReadJournal
from r3.economy import cash_capacity

KST=timezone(timedelta(hours=9))


def summarize(source_path):
    source=read_json(source_path)
    board=source['responses']['/api/board']['data']
    end=source['responses']['/api/board']['finished_at']
    own=next(r for r in board['shops'] if r['name']=='seochanit')
    compact=lambda r:{k:r.get(k) for k in ['name','book','revenue','profit','deals','stock_units','stock_cost','stars_rank','refunds','all','alive']}
    scored=[r for r in board['shops'] if type(r.get('all')) in (int,float)]
    players=[r for r in board['players'] if r.get('student') and type(r.get('student_shop')) in (int,float)]
    player=next(r for r in board['players'] if r['player']=='seochanit')
    baseline_paths=['logs/peer-market-source-20261106T031235682501Z.json',
                    'logs/autonomous-deploy-before-20261106T042821162882Z.json']
    comparisons=[]
    for path in baseline_paths:
        old=read_json(ROOT/path)
        if 'responses' in old:
            oldboard=old['responses']['/api/board']['data'];start=old['responses']['/api/board']['finished_at']
        else:oldboard=old['board'];start=old['at']
        hours=(end-start)/3600
        before={r['name']:r for r in oldboard['shops']}
        rows=[]
        for name in ['seochanit','inextro','yms979','Kiro729','totato2203-r3','buzllightyear']:
            after=next((r for r in board['shops'] if r['name']==name),None)
            if name not in before or not after:continue
            prior=before[name]
            changes={k:after[k]-prior[k] for k in ['book','revenue','deals','stock_units','stock_cost','all','refunds']
                     if type(after.get(k)) in (int,float) and type(prior.get(k)) in (int,float)}
            rows.append({'name':name,'before':compact(prior),'after':compact(after),'changes':changes,
                         'book_per_hour':changes.get('book',0)/hours,'revenue_per_hour':changes.get('revenue',0)/hours,
                         'deals_per_hour':changes.get('deals',0)/hours})
        comparisons.append({'source':path,'start':start,'end':end,'hours':hours,'rows':rows})
    baseline=read_json(ROOT/baseline_paths[1]);start=baseline['at']
    inv_path=ROOT/'.market/inventory.json';inv=read_json(inv_path)
    local_end=min(time.time(),inv_path.stat().st_mtime)
    units=inv['units'];catalog=read_json(ROOT/'.market/catalog.json',{})
    groups=defaultdict(list)
    for u in units:groups[u['sku']].append(u)
    stock=[{'sku':sku,'name':us[0]['name'],'qty':len(us),'cost':sum(u['cost'] for u in us),
            'oldest_hours':(local_end-min(u['supplied_at'] for u in us))/3600,
            'grades':dict(Counter(u['grade'] for u in us)),
            'nearest_expiry_hours':min((u['expires_at']-local_end)/3600 for u in us if u.get('expires_at'))
               if any(u.get('expires_at') for u in us) else None} for sku,us in groups.items()]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,local_end)
        def events(kind,since,until=local_end):
            return [dict(json.loads(d),_ts=t) for t,d in c.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts',(kind,since,until))]
        sales=events('shop_settled',start)
        paid={e['unit']:e for e in sales}
        known=[s for s in sales if type(s.get('cost')) in (int,float)]
        all_owned={json.loads(d).get('unit') for d, in c.execute('SELECT data FROM events WHERE kind=?',('shop_settled',))}
        refunds=[e for e in events('public_broken',start) if e.get('unit') in all_owned and (e.get('refunded') or 0)>0]
        counts=Counter(e['sku'] for e in sales)
        own_urls={own['card']['url'].rstrip('/'),baseline['health']['url'].rstrip('/')}
        log=read_json(ROOT/'.market/round3-watch.json',{}).get('market_log')
        supplies=[]
        if log:
            # This deployment began Nov 6 and the diagnostic covers that day's
            # current session only; old multiday consoles are not concatenated.
            with open(log,encoding='utf-8',errors='replace') as f:
                for line in f:
                    m=re.search(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x .* for (\d+), balance (\d+)',line)
                    if not m:continue
                    at=datetime.fromisoformat('2026-11-06T'+m[1]).replace(tzinfo=KST).timestamp()
                    if start<at<=local_end:supplies.append({'at':at,'qty':int(m[2]),'cost':int(m[3])})
        initial=baseline['inventory']['units'];initial_ids={u['unit'] for u in initial};current_ids={u['unit'] for u in units}
        expired=[u for u in initial if u.get('expires_at') and u['expires_at']<=local_end and
                 u['unit'] not in current_ids and u['unit'] not in paid]
        added=sum(s['qty'] for s in supplies)
        flow={'start':start,'end':local_end,'initial_stock':len(initial),'successful_supply_units':added,
              'supply_cost':sum(s['cost'] for s in supplies),'supply_calls':len(supplies),'paid_sales':len(sales),
              'identified_expired_baseline_units':len(expired),'current_stock':len(units),
              'unexplained_net_reduction':len(initial)+added-len(sales)-len(expired)-len(units)}
        cap=cash_capacity(inv,j,policy(),local_end)
        result={'source':str(source_path.relative_to(ROOT)),'public_cutoff_kst':datetime.fromtimestamp(end,KST).isoformat(),
                'own':compact(own),'rank':1+sum(r['all']>own['all'] for r in scored),'scored_shops':len(scored),
                'class_rank':1+sum(r['student_shop']>player['student_shop'] for r in players),
                'class_shops':len(players),'class_score':player['student_shop'],
                'comparisons':comparisons,'local_cutoff_kst':datetime.fromtimestamp(local_end,KST).isoformat(),
                'unit_flow':flow,'cash_balance':inv['balance'],'cash_capacity':cap,
                'local_stock_cost':sum(u['cost'] for u in units),'stock_skus':len(groups),
                'stock':sorted(stock,key=lambda r:r['cost'],reverse=True),
                'stock_kind':dict(Counter(u['kind'] for u in units)),
                'cost_above_75pct_list':{'units':sum(u['cost']>.75*u['list_price'] for u in units),
                    'cost':sum(u['cost'] for u in units if u['cost']>.75*u['list_price'])},
                'sales':{'revenue':sum(e['price'] for e in sales),'known_cost_sales':len(known),
                    'gross_margin_before_later_costs':sum(e['price']-e['cost'] for e in known),
                    'below_cost_count':sum(e['price']<e['cost'] for e in known),
                    'below_cost_loss':sum(e['cost']-e['price'] for e in known if e['price']<e['cost']),
                    'refunds_confirmed_in_period':sum(e['refunded'] for e in refunds),
                    'refund_count':len(refunds),'latest':sales[-5:],
                    'top_skus':[{'sku':sku,'name':catalog.get(sku,{}).get('name'),
                        'deals':n,'gross_margin':sum(e['price']-e.get('cost',e['price']) for e in sales if e['sku']==sku)}
                        for sku,n in counts.most_common(8)]},
                'health':read_json(ROOT/'.market/shop-health.json',{}),
                'control':{k:j.get('market_control',{}).get(k) for k in ['status','at','reasons','pause_restock']},
                'supply_window':inv['restock'],
                'limits':['Public board, local inventory and baseline are independently timed snapshots.',
                    'Gross margin excludes future refunds, storage and expiry.',
                    'Unit-flow residual is not labeled waste without direct evidence.',
                    'Cost above 75% is a discount-capacity screen, not proof every buyer requires 25% discount.',
                    'Peer hourly rates describe this interval, not a forecast or causal comparison.']}
    return result


if __name__=='__main__':
    result=summarize(ROOT/sys.argv[1])
    path=ROOT/'logs'/('shop-progress-review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path);print(json.dumps(result,ensure_ascii=True))
