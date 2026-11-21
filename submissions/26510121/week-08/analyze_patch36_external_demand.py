"""Observe external buying mix before/after patch 3.6; not causal preference proof."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import statistics
from r3.state import ROOT,read_json

KST=timezone(timedelta(hours=9))


def summarize(rows):
    result={}
    for kind in ('food','goods'):
        group=[r for r in rows if r['kind']==kind];n=len(group)
        result[kind]={'deals':n,'buyers':len({r['buyer'] for r in group}),
            'grade_mix':dict(Counter(r.get('grade') for r in group)),
            'grade_a_share':sum(r.get('grade')=='A' for r in group)/n if n else None,
            'full_6h_warranty_share':sum((r.get('warranty_hours') or 0)>=6 for r in group)/n if n else None,
            'median_paid_to_list':statistics.median(r['price_ratio'] for r in group) if n else None,
            'by_grade':{grade:{'deals':len(g),'median_paid_to_list':statistics.median(r['price_ratio'] for r in g)}
                for grade in ('A','B','C') if (g:=[r for r in group if r.get('grade')==grade])}}
    return result


def main():
    study=read_json(ROOT/'logs/v22-progress-analysis-20261112T103035363243Z.json')
    hi=datetime.fromisoformat(study['operations']['v22']['end_kst']).timestamp()
    lo=datetime.fromisoformat(study['operations']['v22']['start_kst']).timestamp();span=hi-lo
    before_hi=datetime(2026,11,12,17,23,tzinfo=KST).timestamp()
    catalog=read_json(ROOT/'.market/catalog.json');periods={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        own_sales=[]
        for at,raw in db.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=?',('shop_settled',lo,hi)):
            r=json.loads(raw);p=catalog.get(r.get('sku'),{});L=p.get('list_price');C=r.get('cost')
            if type(L) in (int,float) and type(C) in (int,float) and 0<=C<L:
                own_sales.append(dict(r,kind=p.get('kind'),list_price=L,price_ratio=r['price']/L,
                    cost_ratio=C/L,healthy_margin_share=min(1,(L-r['price'])/(L-C))))
        own_units={r[0] for r in db.execute("SELECT json_extract(data,'$.unit') FROM events WHERE kind=?",('shop_settled',))}
        for label,a,b in [('before_patch',before_hi-span,before_hi),('after_patch',lo,hi)]:
            rows=[]
            for at,raw in db.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=?',('public_settle',a,b)):
                row=json.loads(raw);sku=row.get('sku') or row.get('item');product=catalog.get(sku,{})
                price=row.get('amount',row.get('price'));L=product.get('list_price')
                if row.get('buyer')=='seochanit' or row.get('unit') in own_units:continue
                if type(price) not in (int,float) or type(L) not in (int,float) or L<=0:continue
                rows.append(dict(row,kind=product.get('kind'),sku=sku,price_ratio=price/L))
            periods[label]={'start_kst':datetime.fromtimestamp(a,KST).isoformat(),'end_kst':datetime.fromtimestamp(b,KST).isoformat(),
                'summary':summarize(rows),'rows':rows}
    buyers=sorted({r['buyer'] for r in periods['before_patch']['rows']} & {r['buyer'] for r in periods['after_patch']['rows']})
    matched=[]
    for buyer in buyers:
        a=[r for r in periods['before_patch']['rows'] if r['buyer']==buyer]
        b=[r for r in periods['after_patch']['rows'] if r['buyer']==buyer]
        if min(len(a),len(b))<10:continue
        old,new=summarize(a),summarize(b)
        matched.append({'buyer':buyer,'before':old,'after':new})
    common={r['buyer'] for r in matched}
    result={'windows':{k:{key:v[key] for key in ('start_kst','end_kst','summary')} for k,v in periods.items()},
        'own_shop_healthy_score_scenarios':{kind:{'sales':len(g),
            'mean_healthy_saved':statistics.mean(r['healthy_margin_share'] for r in g),
            'median_healthy_saved':statistics.median(r['healthy_margin_share'] for r in g),
            'median_price_ratio':statistics.median(r['price_ratio'] for r in g),
            'grade_mix':dict(Counter(r.get('grade') for r in g)),
            'full_6h_warranty_share':sum((r.get('warranty_hours') or 0)>=6 for r in g)/len(g)}
            for kind in ('food','goods') if (g:=[r for r in own_sales if r['kind']==kind])},
        'same_buyer_cohort':{'buyers':len(common),
            **{k:summarize([r for r in v['rows'] if r['buyer'] in common]) for k,v in periods.items()}},
        'per_buyer':matched,'controls':'Exclude our Buyer and own Shop units; normalize price by SKU list price; same buyer >=10 deals each window.',
        'limits':['No unit costs, refused offers, inventories or private policies are observed.',
            'Different time windows, SKU mix, supply and availability confound apparent preference shifts.',
            'The patch changes incentives, not proof every participant updated its code.',
            'Before window ends at 17:23, prior to the old-score 17:28 baseline and first local new-rule observation at 17:41.'],
        'market_transactions':0,'model_calls':0}
    path=ROOT/'logs'/('v22-external-demand-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path);print(json.dumps({k:result[k] for k in ('windows','same_buyer_cohort','own_shop_healthy_score_scenarios')},ensure_ascii=True))


if __name__=='__main__':main()
