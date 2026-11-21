"""Read-only post-release evidence; no synthetic orders or payment retries."""
from collections import Counter
from datetime import datetime,timezone
import json
import sqlite3
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,read_json,atomic_json


def main():
    now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,now)
        epoch=j.get('buyer_revision_epoch',0);version=j.get('adaptive:buyer',{}).get('version')
        def fresh(kind):return [r for r in j.events(kind,2000) if epoch<=r['_ts']<=now]
        purchases=[p for p in fresh('purchase') if p.get('strategy_version')==version]
        quotes=fresh('quote_comparison');attempts=fresh('attempt')
        result={'at':now,'revision':j.get('buyer_strategy_revision'),'version':version,'epoch':epoch,
            'elapsed_seconds':now-epoch,'runtime':read_json(ROOT/'.market/runtime.json',{}),
            'health':read_json(ROOT/'.market/shop-health.json',{}),
            'shop_strategy_revision':j.get('shop_strategy_revision'),
            'orders':dict(Counter(e.get('kind') for e in fresh('buyer_order'))),
            'paid':dict(Counter(p.get('kind') for p in purchases)),
            'purchases':[{k:p.get(k) for k in ['order','sku','kind','price','value','grade','warranty_hours','ledger',
                                             'initial_saved_contribution','strategy_version']} for p in purchases],
            'quotes':[{'order':e['order'],'sku':e['sku'],'ask':e['ask'],'feasible_at_ask':e['feasible_at_ask'],
                       'assessment':e['assessment']} for e in quotes],
            'clarifications':len(fresh('buyer_terms_clarified')),
            'attempt_reasons':dict(Counter(e.get('reason') or e.get('error_type') or e.get('kind') for e in attempts)),
            'market_results':[{k:e.get(k) for k in ['order','verified','state','strategy_version']} for e in fresh('market_result')],
            'public_quality':{k:v for k,v in j.get('buyer_public_quality_v11',{}).items() if k!='groups'},
            'caution':'Early quotes and initial Saved are execution evidence; goods quality needs six-hour maturity.'}
    path=ROOT/'logs'/('buyer-v11-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,result);print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k not in ['quotes','purchases','market_results']},ensure_ascii=True))


if __name__=='__main__':main()
