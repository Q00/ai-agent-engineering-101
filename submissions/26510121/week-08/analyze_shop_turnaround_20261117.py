"""Read-only forecast audit: retain real accounting evidence, never buy stock."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime,timezone
import copy
import json
import sqlite3
import time
from r3.state import ROOT,Journal,read_json


class ShadowJournal(Journal):
    def __init__(self):
        self.md=ROOT/'.market';self.db=self.md/'r3.sqlite3'
        self.overlay={};self.recorded=[]

    @contextmanager
    def connect(self,*,timeout=15):
        db=sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=timeout)
        try:yield db
        finally:db.close()

    def get(self,key,default=None):
        return self.overlay.get(key,super().get(key,default))

    def put(self,key,value):self.overlay[key]=copy.deepcopy(value)

    def add(self,kind,data,**kwargs):self.recorded.append((kind,copy.deepcopy(data)))

    observe=add


def main():
    from r3 import departments,economy
    from r3.adaptive import effective
    from r3.shop_growth import poisson_tail
    j=ShadowJournal();cfg,_=effective(j,'shop');now=time.time()
    inv=j.snapshot('inventory.json',{});catalog=j.snapshot('catalog.json',{})
    original_window=copy.deepcopy(inv.get('restock'))
    inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    # This is analysis, not a supplier authorization or an override of execution guards.
    control=j.get('market_control',{});j.put('market_control',dict(control,pause_restock=False))
    captured={};original=departments.procurement_department
    def capture(candidates,inventory,capacity,config,journal,products,snapshot_at,**kwargs):
        captured.update(candidates=copy.deepcopy(candidates),capacity=capacity)
        return original(candidates,inventory,capacity,config,journal,products,snapshot_at,**kwargs)
    departments.procurement_department=capture
    started=time.monotonic()
    baseline=economy.restock_candidates(inv,catalog,j.snapshot('demand.json',[]),j,cfg,now)
    accounting=j.get(departments.KEY,{})
    external_query="""SELECT coalesce(json_extract(data,'$.sku'),json_extract(data,'$.item')),max(ts)
        FROM events WHERE kind='public_settle' AND ts>=? AND ts<=?
        AND json_extract(data,'$.buyer')!='seochanit' GROUP BY 1"""
    with j.connect() as db:
        external_latest=dict(db.execute(external_query,(now-21600,now)))
    held=Counter(u['sku'] for u in inv.get('units',[]) if not u.get('expires_at') or u['expires_at']>now)
    rows=[]
    for c in captured.get('candidates',[]):
        e=c['evidence'];g=accounting.get('skus',{}).get(c['sku'],{});kind=catalog[c['sku']]['kind']
        arrivals=e['arrival_rate_scenario']*e['planning_hours'];cost=e['scenario_unit_cost'];price=e['resale_price_scenario']
        refund=max(e.get('refund_upper_scenario',e.get('refund_stress',0)),g.get('refunds',0)/max(1,g.get('price',0)))
        deltas=[];revenues=[]
        for q in range(1,c['qty']+1):
            p=poisson_tail(arrivals,held[c['sku']]+q)
            delta=p*(price*(1-refund)-cost)-(cost*.01*e['planning_hours']/24 if kind=='goods' else (1-p)*cost)
            if delta<=0:break
            deltas.append(delta);revenues.append(p*price*(1-refund))
        rows.append({'sku':c['sku'],'name':catalog[c['sku']].get('name'),'kind':kind,
            'held':held[c['sku']],'candidate_qty':c['qty'],'positive_marginal_qty':len(deltas),
            'forecast_book_without_sku_gate':sum(deltas),'forecast_book_first':deltas[0] if deltas else 0,
            'forecast_revenue_without_sku_gate':sum(revenues),
            'mature_sales':g.get('mature_sales',0),'mature_margin':g.get('margin_after_refunds',0),
            'reviews':g.get('reviews',0),'low_reviews':g.get('low_reviews',0),
            'unit_cost_scenario':cost,'upper_unit_cost':e['worst_case_unit_cost'],
            'price_scenario':price,'refund_upper':refund,'arrival_rate':e['arrival_rate_scenario'],
            'external_samples':e['public_sku_trades'].get('external_samples',0),
            'external_buyers':e['public_sku_trades'].get('external_buyers',[]),
            'latest_external_observation':external_latest.get(c['sku']),
            'resale_terms':e.get('resale_terms_scenario'),
            'capital':c['capital_commitment'],'max_cost':c['max_cost'],
            'upper_cost_profit_stress':e.get('upper_cost_profit_stress')})
    rows.sort(key=lambda r:r['forecast_book_without_sku_gate'],reverse=True)
    out={'at':now,'finished_at':time.time(),'seconds':time.monotonic()-started,
        'actual_supplier_window':original_window,'execution_allowed':False,
        'config_revision':cfg['execution_revision'],'baseline_buy':[{'sku':c['sku'],'qty':c['qty'],'book':c['expected_delta']['book']} for c in baseline],
        'baseline_approval':j.get('company-procurement:v30'),
        'candidate_counts':dict(Counter(r['kind'] for r in rows)),
        'candidates':rows,'seller_risk_cohorts':j.get('shop_quality_cohorts_v28'),
        'financial_capacity':captured.get('capacity'),
        'external_freshness_query':external_query,
        'limits':['Unrestricted calculations remove only the SKU maturity veto for diagnosis, not for trading.',
            'Past public prices and demand rates are scenarios, not guaranteed future orders or profit.',
            'No requests were made to suppliers and no live journal records were written.']}
    path=ROOT/'logs'/('shop-turnaround-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path);print('SECONDS',out['seconds'],'COUNTS',out['candidate_counts'])
    print('BASELINE',json.dumps(out['baseline_buy'],ensure_ascii=True))
    print('GOODS_TOP',json.dumps([r for r in rows if r['kind']=='goods'][:8],ensure_ascii=True))


if __name__=='__main__':main()
