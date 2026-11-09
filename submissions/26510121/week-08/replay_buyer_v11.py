"""Replay actual captured quote declarations and public risk without trading."""
from datetime import datetime,timezone
import json
import sqlite3
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,read_json,policy
from r3.protocol import quote,quote_terms
from r3.market_memory import buyer_assessment
from r3.buyer_quality import cohorts


def main():
    now=time.time();cfg=dict(policy(),buyer_market_quality_enabled=True,buyer_contract_routing_enabled=True)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,now);cat=j.snapshot('catalog.json',{})
        orders={r['order']:r for r in j.events('buyer_order',2000)}
        census=cohorts(j,now);out=[]
        for wire in j.events('buyer_quote_wire',300):
            if wire.get('state')!='TASK_STATE_INPUT_REQUIRED':continue
            order=orders.get(wire['order'],{})
            t={'metadata':wire.get('metadata',{}),'status':{'message':wire.get('message',{})}}
            try:
                terms=quote_terms(t);ask=quote(t);sku=terms.get('sku')
                if sku not in order.get('acceptable',[]) or sku not in cat:continue
                p=dict(cat[sku],sku=sku)
                old=buyer_assessment(p,order,terms,j,dict(cfg,buyer_market_quality_enabled=False,
                                    buyer_contract_routing_enabled=False),shop=wire['shop'])
                new=buyer_assessment(p,order,terms,j,cfg,shop=wire['shop'])
                out.append({'order':wire['order'],'shop':wire['shop'],'sku':sku,'kind':p['kind'],
                            'ask':ask,'grade':terms.get('grade'),'warranty':terms.get('warranty_hours'),
                            'legacy_risk_cap':old['cap'],'new_cap':new['cap'],'new_risk':new['risk_evidence'],
                            'price_fits_new_cap':ask<=new['cap'],'conditional_terms':new['conditional_terms']})
            except ValueError as exc:out.append({'order':wire['order'],'error':str(exc)})
    result={'at':datetime.now(timezone.utc).isoformat(),'NOT_EXECUTED':True,
            'census':{k:v for k,v in census.items() if k!='groups'},'quotes':out,
            'limits':['Wire observations can include several quotes per order.',
                      'Historical quoted prices and cohort risk are not promises of availability or future quality.',
                      'Conditional terms still require the actual market certificate and warranty before payment.']}
    path=ROOT/'logs'/('buyer-v11-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path);print('CENSUS',json.dumps(result['census']))
    print(json.dumps(out,ensure_ascii=True)[:9000])


if __name__=='__main__':main()
