"""Repeat a frozen, non-executing comparison without changing runtime policy."""
from datetime import datetime,timezone
import argparse
import json
from r3.state import ROOT
from r3.shop_reentry_research import compare

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    args=parser.parse_args()
    audit=json.loads((ROOT/args.source).read_text(encoding='utf8'))
    result=compare(audit);result['source']=args.source
    result['forecast_multiplier']=result['proposed_book']/result['baseline_book'] if result['baseline_book']>0 else None
    path=ROOT/'logs'/('shop-reentry-preparation-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({k:result[k] for k in ('baseline_book','proposed_book','proposed_capital','forecast_multiplier','execution_allowed')}))
