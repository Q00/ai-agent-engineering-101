"""Read-only A-grade inventory-option research with explicit stress cases."""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import json
import time
from analyze_shop_turnaround_20261117 import ShadowJournal
from r3.state import ROOT,policy
from r3.finance import planning_hours,reserve_days,surge_factor
from r3.risk import shop_refund_risk
from r3.shop_option_research import option_rows


def main():
    j=ShadowJournal();cfg=policy();now=time.time();catalog=j.snapshot('catalog.json',{});inv=j.snapshot('inventory.json',{})
    own_url=j.snapshot('shop-health.json',{}).get('url','').rstrip('/')
    grouped=defaultdict(lambda:{'prices':[],'buyers':set(),'latest':0})
    with j.connect() as db:
        query='SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts'
        for ts,raw in db.execute(query,('public_settle',now-21600,now)):
            e=json.loads(raw);sku=e.get('sku',e.get('item'));p=e.get('amount',e.get('price'))
            if (catalog.get(sku,{}).get('kind')!='goods' or e.get('buyer')=='seochanit'
                    or str(e.get('shop','')).rstrip('/')==own_url or e.get('grade')!='A'
                    or e.get('warranty_hours')!=6 or type(p) not in (int,float) or p<=0):continue
            row=grouped[sku];row['prices'].append(p);row['buyers'].add(e.get('buyer'));row['latest']=ts
    competition=j.snapshot('market_stock_counts.json',{})
    current_counts=competition.get('counts',{}) if 0<=now-competition.get('at',0)<120 else {}
    observed={sku:{'samples':len(g['prices']),'buyers':len(g['buyers']),
        'price_p25':sorted(g['prices'])[int((len(g['prices'])-1)*.25)],
        'age_seconds':now-g['latest'],'competitors':current_counts.get(sku,1),
        'surge_factor':surge_factor(inv,sku)} for sku,g in grouped.items()}
    held=Counter(u['sku'] for u in inv.get('units',[]) if u.get('kind')=='goods' and u.get('grade')=='A')
    risk=shop_refund_risk(j,'A',6,cfg,now)
    scenarios={}
    for label,ratio,mean,scale in [('upper_cost_upper_failure',.75,False,1),
            ('mid_cost_upper_failure',.65,False,1),('upper_cost_validated_mean',.75,True,1),
            ('mid_cost_validated_mean',.65,True,1),
            ('mid_cost_upper_failure_half_demand',.65,False,.5)]:
        rows=option_rows(observed,catalog,held,risk,planning_hours(cfg,now),reserve_days(cfg,now),
            cost_ratio=ratio,use_mean=mean,rate_scale=scale)
        top=rows[:10]
        scenarios[label]={'candidate_count':len(rows),'top10_forecast_book':sum(r['forecast_book'] for r in top),
            'top10_capital':sum(r['capital'] for r in top),'top10_units':sum(r['qty'] for r in top),'top10':top}
    out={'at':now,'finished_at':time.time(),'execution_allowed':False,'risk':risk,
        'contract_observed_skus':len(observed),'fresh_competition_counts':bool(current_counts),
        'scenarios':scenarios,'query':query,
        'limitations':['Historical public contract prices do not establish future buyer conversion.',
            'Cost .65 is a midpoint scenario, not a known supplier distribution.',
            'A-grade pooled risk may not transfer to a new SKU; forecasts are not realized uplift.',
            'B/C draws are held at cost in this model; their sales are not counted as upside.',
            'No supplier request, live policy change, or live journal write was made.']}
    path=ROOT/'logs'/('shop-option-preparation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:{a:v[a] for a in ('candidate_count','top10_forecast_book','top10_capital','top10_units')} for k,v in scenarios.items()}))


if __name__=='__main__':main()
