"""Capture public market GETs and compare unchanged entities across frozen boards.

No registration, credentials, trading, model invocation or policy mutations.
Rates are short-window observations; catch-up arithmetic is a scenario only.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
import sys
import time
import httpx
from r3.state import ROOT, read_json


def capture():
    def get(path):
        started=time.time()
        r=httpx.get('https://market.ourolabs.ai'+path,timeout=20,trust_env=False)
        r.raise_for_status()
        return path,{'started_at':started,'finished_at':time.time(),'status':r.status_code,
                     'data':r.text if path.endswith('.md') else r.json()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses=dict(pool.map(get,['/agent.md','/api/board','/api/shops']))
    return {'checked_at':datetime.now(timezone.utc).isoformat(),'responses':responses}


def analyze(source):
    board=source['responses']['/api/board']['data']
    end=source['responses']['/api/board']['finished_at']
    keys={'shops':'name','buyers':'name','players':'player'}
    fields={'shops':['book','revenue','deals','all','stock_units','stock_cost','stars_rank','refunds'],
            'buyers':['saved','surplus','deals','forum','posts','all'], 'players':['total']}
    latest={role:{r[keys[role]]:r for r in board[role]} for role in keys}
    comparisons=[]
    for filename in ['autonomous-deploy-before-20261105T045909708231Z.json',
                     'autonomous-deploy-before-20261105T052345470426Z.json']:
        old=read_json(ROOT/'logs'/filename,{})
        hours=(end-old['at'])/3600
        result={'source':'logs/'+filename,'start':old['at'],'end':end,'hours':hours,'roles':{}}
        for role,key in keys.items():
            oldrows={r[key]:r for r in old['board'][role]}
            rows=[]
            for name,current in latest[role].items():
                if name not in oldrows:continue
                before=oldrows[name];changes={}
                for field in fields[role]:
                    if field=='saved' and not all(type(r.get(field)) in (int,float) and -1<=r[field]<=1
                                                 for r in [before,current]):continue
                    if type(current.get(field)) in (int,float) and type(before.get(field)) in (int,float):
                        delta=current[field]-before[field]
                        changes[field]={'before':before[field],'after':current[field],
                                        'delta':delta,'per_hour':delta/hours}
                rows.append({'name':name,'house':current.get('house',False),'changes':changes})
            result['roles'][role]=rows
        comparisons.append(result)
    ranks={}
    for role,metric in [('shops','all'),('buyers','all'),('players','total')]:
        registered=list(latest[role].values())
        rows=[r for r in registered if type(r.get(metric)) in (int,float)]
        me=latest[role]['seochanit']
        ahead=sorted([r for r in rows if r.get(metric,0)>me[metric]],key=lambda r:r[metric])
        def compact(r):return {k:r.get(k) for k in [keys[role],*fields[role]]}
        ranks[role]={'rank':1+len(ahead),'scored_population':len(rows),
            'registered_population':len(registered),'own':compact(me),
            'nearest_ahead':[compact(r) for r in ahead[:3]],
            'top5':[compact(r) for r in sorted(rows,key=lambda r:r[metric],reverse=True)[:5]]}
    component_targets={}
    for role,metrics in [('shops',['book','revenue']),('buyers',['saved','surplus','forum','deals','posts'])]:
        me=latest[role]['seochanit'];component_targets[role]={}
        for metric in metrics:
            rows=[r for r in latest[role].values() if type(r.get(metric)) in (int,float)]
            ahead=sorted((r for r in rows if r[metric]>me[metric]),key=lambda r:r[metric])
            component_targets[role][metric]={'current':me[metric],
                'next':[{keys[role]:r[keys[role]],'value':r[metric],'gap':r[metric]-me[metric]} for r in ahead[:3]]}
    shops=source['responses']['/api/shops']['data']['shops']
    assortment=[{'name':s['name'],'skus':len([q for q in s.get('stock',{}).values() if q>0]),
                 'units':sum(s.get('stock',{}).values())} for s in shops]
    conn=sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True)
    conn.execute('BEGIN')
    epoch=json.loads(conn.execute('SELECT value FROM kv WHERE key=?',('objective_epoch',)).fetchone()[0])
    def events(kind):
        return [json.loads(d) for d, in conn.execute('SELECT data FROM events WHERE kind=? AND ts>=? AND ts<=?',(kind,epoch,end))]
    purchases={e['order']:e for e in events('purchase')}
    quality={e['order']:e for e in events('outcome')}
    own_mix={kind:{'purchases':sum(e.get('kind')==kind for e in purchases.values()),
        'initial_surplus':sum(e.get('value',0)-e['price'] for e in purchases.values() if e.get('kind')==kind)} for kind in ['goods','food']}
    control=json.loads(conn.execute('SELECT value FROM kv WHERE key=?',('market_control',)).fetchone()[0])
    conn.close()
    return {'checked_at':source['checked_at'],'comparison_windows':comparisons,'ranks':ranks,
            'next_raw_component_thresholds':component_targets,'assortment':assortment,
            'own_purchase_mix_since_epoch':own_mix,'quality':{'epoch':epoch,'hours_since_epoch':(end-epoch)/3600,
                'purchases':len(purchases),'matured':sum(e.get('matured',False) for oid,e in quality.items() if oid in purchases)},
            'controller':{k:control.get(k) for k in ['status','pause_restock','book','suggestions','cash_capacity']},
            'limits':['Baseline capture timestamps approximate board time (poll interval up to one minute).',
                'Same-name matched entities only in growth comparison; new entrants excluded from rates.',
                'Ranks use the published ALL/total values, including published house participants.',
                'Raw component thresholds are not exact predictions of official ALL.',
                'Short windows straddle supply cycles and include startup downtime; rates are not forecasts.',
                'Buyer initial surplus can be revised by quality outcomes; no guarantee of final reward.']}


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    source=capture();source_path=ROOT/'logs'/('peer-market-source-'+stamp+'.json')
    source_path.write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')
    result=analyze(source);result['source']=str(source_path.relative_to(ROOT))
    output=ROOT/'logs'/('peer-market-analysis-'+stamp+'.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',source_path);print('ANALYSIS',output)
    for window in result['comparison_windows']:
        print('WINDOW',window['hours'])
        for role,metric in [('shops','book'),('buyers','surplus')]:
            rows=window['roles'][role]
            chosen={r['name'] for r in result['ranks'][role]['top5']}
            chosen.add('seochanit')
            print(role,json.dumps([r for r in rows if r['name'] in chosen],ensure_ascii=False))
    print('RANKS',json.dumps(result['ranks'],ensure_ascii=False))
    print('MIX',json.dumps(result['own_purchase_mix_since_epoch']))
    print('THRESHOLDS',json.dumps(result['next_raw_component_thresholds']))
