"""Freeze local evidence and replay procurement without sending market orders."""
from datetime import datetime,timezone
import json
import sqlite3
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,read_json,policy
from r3.economy import restock_candidates


def main():
    cutoff=time.time();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    files={name:read_json(ROOT/'.market'/name,{}) for name in
           ['inventory.json','catalog.json','board.json','market_stock_counts.json','registry.json']}
    cfg=policy();source={'journal_cutoff':cutoff,'local_files':files,'policy':cfg}
    path=ROOT/'logs'/('championship-replay-source-'+stamp+'.json')
    path.write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')
    variants={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        class Frozen(ReadJournal):
            def snapshot(self,name,default=None):return files.get(name,default)
        for name,patch in [('v7',{'shop_championship_enabled':False}),
                           ('new_economics_velocity',{'shop_championship_enabled':True,'shop_championship_allocation_enabled':False}),
                           ('new_portfolio',{'shop_championship_enabled':True,'shop_championship_allocation_enabled':True})]:
            j=Frozen(c,cutoff);j.put('merchant_context_v5',{});j.put('market_control',{'pause_restock':False,'capital_fraction':1})
            inv=dict(files['inventory.json'],restock={'open':True,'opens_at':cutoff-1,'closes_at':cutoff+180})
            started=time.monotonic();rows=restock_candidates(inv,files['catalog.json'],[],j,dict(cfg,**patch),cutoff)
            variants[name]={'seconds':time.monotonic()-started,'lines':len(rows),'units':sum(r['qty'] for r in rows),
                            'capital':sum(r['capital_commitment'] for r in rows),
                            'book_scenario':sum(r['expected_delta']['book'] for r in rows),
                            'book_per_holding_hour_scenario':sum(d['book']/max(1/3,d['holding']) for r in rows
                                    for d in r['evidence'].get('marginal_units',[])),
                            'items':[{'sku':r['sku'],'name':files['catalog.json'][r['sku']]['name'],'qty':r['qty'],
                                      'capital':r['capital_commitment'],'book':r['expected_delta']['book'],
                                      'allocation':r['allocation_book'],'evidence':r['evidence']} for r in rows]}
    out=ROOT/'logs'/('championship-replay-'+stamp+'.json')
    out.write_text(json.dumps({'source':str(path.relative_to(ROOT)),'NOT_EXECUTED':True,'variants':variants,
                               'limits':['Supply and control are hypothetically open/normal.',
                                         'Forecast changes are not measured profits or rank gains.',
                                         'Only the two new-economics variants share identical economics and capacity.']},
                              ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path);print('REPLAY',out)
    print(json.dumps({k:{f:v for f,v in r.items() if f!='items'} for k,r in variants.items()}))


if __name__=='__main__':main()
