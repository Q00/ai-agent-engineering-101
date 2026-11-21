"""Frozen score decomposition and exact local sale/review joins; no market actions."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import statistics
from r3.state import ROOT,read_json

KST=timezone(timedelta(hours=9))
SOURCE='logs/peer-market-source-20261112T124342220675Z.json'
BASE='logs/autonomous-deploy-before-20261112T112620171814Z.json'


def at(t):return datetime.fromtimestamp(t,KST).isoformat()


def components(board):
    rows=[r for r in board['shops'] if type(r.get('all')) in (int,float)]
    me=next(r for r in rows if r['name']=='seochanit');n=len(rows)
    out={}
    for key,w in [('book',.5),('revenue',.3),('stars_rank',.2)]:
        above=sum(r[key]>me[key] for r in rows);ties=sum(r[key]==me[key] for r in rows)
        percentile=(sum(r[key]<me[key] for r in rows)+(ties-1)/2)/(n-1)
        out[key]={'value':me[key],'strict_rank':above+1,'ties_including_self':ties,
            'midrank_percentile':percentile,'weighted_component':w*percentile}
    return {'population':n,'official_all':me['all'],'all_rank':1+sum(r['all']>me['all'] for r in rows),
        'raw_stars':me.get('stars'),'review_count':me.get('reviews'),'parts':out,
        'reconstructed_all':sum(r['weighted_component'] for r in out.values())}


def main():
    source=read_json(ROOT/SOURCE);board=source['responses']['/api/board']['data']
    end=source['responses']['/api/board']['finished_at'];base=read_json(ROOT/BASE)
    runtime=read_json(ROOT/'.market/runtime.json');start=runtime['started_at'];duration=end-start
    catalog=read_json(ROOT/'.market/catalog.json');before,after=components(base['board']),components(board)
    exact_delta={k:after['parts'][k]['weighted_component']-before['parts'][k]['weighted_component'] for k in after['parts']}
    assert round(before['reconstructed_all'],3)==before['official_all']
    assert round(after['reconstructed_all'],3)==after['official_all']
    assert abs(sum(exact_delta.values())-(after['reconstructed_all']-before['reconstructed_all']))<1e-12
    windows={};reviews_by_owner={};low=[]
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def rows(kind,lo=0,hi=end):
            return [dict(json.loads(raw),_event_id=eid,_ts=ts) for eid,ts,raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',(kind,lo,hi))]
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}
        allsales=rows('shop_settled');sales_by_ledger={r['_event_id'].partition('settlement:')[2]:r for r in allsales}
        allreviews={str(r.get('ledger')):r for r in rows('shop_review_observed')}
        breaks={r.get('unit'):r for r in rows('public_broken',start-21600)}
        for label,lo,hi in [('before_equal_duration',start-duration,start),('v23',start,end)]:
            sales=[r for r in allsales if lo<=r.get('paid_at',r['_ts'])<=hi]
            ratings=[r for r in allreviews.values() if lo<=r['_ts']<=hi]
            supply=rows('supply_execution_status',lo,hi)
            mix={}
            for kind in ('food','goods'):
                group=[r for r in sales if catalog.get(r.get('sku'),{}).get('kind')==kind]
                mix[kind]={'sales':len(group),'revenue':sum(r['price'] for r in group),
                    'gross_margin':sum(r['price']-(r.get('cost') or 0) for r in group),
                    'grades':dict(Counter(r.get('grade') for r in group)),
                    'warranties':dict(Counter(str(r.get('warranty_hours')) for r in group))}
            windows[label]={'start':at(lo),'end':at(hi),'minutes':(hi-lo)/60,'sales':len(sales),
                'revenue':sum(r['price'] for r in sales),'gross_margin':sum(r['price']-(r.get('cost') or 0) for r in sales),
                'below_cost':sum(r['price']<(r.get('cost') or 0) for r in sales),
                'rating_observations':len(ratings),'ratings':dict(Counter(str(r['stars']) for r in ratings)),
                'mean_observed_review':statistics.mean(r['stars'] for r in ratings) if ratings else None,
                'supply_completed_qty':sum(r.get('qty',0) for r in supply if r.get('status')=='complete'),
                'supply_status':dict(Counter(r['status'] for r in supply)),'mix':mix}
        grouped=defaultdict(list)
        for r in allreviews.values():
            if r.get('identity_known') and r.get('author'):grouped[r['author']].append(r)
            if start<=r['_ts']<=end and r['stars']<=3:
                sale=sales_by_ledger.get(str(r.get('ledger')),{});broken=breaks.get(sale.get('unit'),{})
                low.append({'review':{k:r.get(k) for k in ('stars','author','ledger','review')},'at':at(r['_ts']),
                    'sale':{k:sale.get(k) for k in ('unit','sku','grade','price','cost','warranty_hours','paid_at')},
                    'break_fact':{k:broken.get(k) for k in ('hours_after_sale','refunded','unit','order')},
                    'exact_sale_joined':bool(sale)})
        for owner,group in grouped.items():
            new=[r for r in group if r['_ts']>=start];old=[r for r in group if r['_ts']<start]
            if new:
                reviews_by_owner[owner]={'old_observed_n':len(old),'new_n':len(new),
                    'old_observed_mean':statistics.mean(r['stars'] for r in old) if old else None,
                    'new_mean':statistics.mean(r['stars'] for r in new),
                    'combined_observed_mean':statistics.mean(r['stars'] for r in group),
                    'new_stars':dict(Counter(str(r['stars']) for r in new))}
        quality=kv('quality-holdout-summary');control=kv('market_control');policy=kv('feedback-last-policy')
        errors={k:len(rows(k,start)) for k in ('cycle_supervisor_error','cycle_supply_error','analytical_role_error','market_control_error','strategy_error')}
    peers=[];old={r['name']:r for r in base['board']['shops']}
    for r in board['shops']:
        if r['name'] in ('seochanit','yms979','inextro','sergiobuilds','moss-and-oak') and r['name'] in old:
            b=old[r['name']];peers.append({'name':r['name'],**{k+'_delta':r[k]-b[k] for k in ('book','revenue','deals','all')},
                'book_per_hour':(r['book']-b['book'])/((end-base['at'])/3600)})
    timeline=[]
    for p in ('logs/autonomous-deploy-before-20261112T053423471758Z.json',
              'logs/autonomous-deploy-before-20261112T082837273128Z.json',BASE):
        snap=read_json(ROOT/p);timeline.append({'source':p,'at':at(snap['at']),**components(snap['board'])})
    timeline.append({'source':SOURCE,'at':at(end),**after})
    result={'source':SOURCE,'baseline':BASE,'start_kst':at(start),'end_kst':at(end),'before':before,'after':after,
        'weighted_score_change':exact_delta,'timeline':timeline,'operations':windows,'peers':peers,
        'new_low_reviews':low,'observed_owner_rating_changes':reviews_by_owner,
        'quality_model':quality,'control':{k:control.get(k) for k in ('at','status','pause_restock','cash_capacity')},
        'operating_policy':{k:policy.get(k) for k in ('at','status','patches')},'errors':errors,
        'time_correction':'v23 start is 20:27 KST, not 21:27 KST; derive from runtime UTC timestamp.',
        'limits':['Percentile reconstruction uses rounded public metrics and midranks; matches both published ALL values.',
            'Shop review ledger observations are incomplete historical coverage; owner means below do not reconstruct the full official account mean.',
            'A negative review is not proof of manipulation. Public break facts and review text are different evidence.',
            'Read-only observations cannot establish the causal effect of v23 versus changing demand, supply or outages.'],
        'market_transactions':0,'model_calls':0}
    path=ROOT/'logs'/('v23-shop-score-drop-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({'score_change':exact_delta,'operations':windows,'peers':peers,'low_reviews':low,'quality_model':quality},ensure_ascii=True))


if __name__=='__main__':main()
