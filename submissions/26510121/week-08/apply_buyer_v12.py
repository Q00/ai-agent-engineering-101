"""Activate only the tested Buyer execution fields; no credentials or trade calls."""
from datetime import datetime,timezone
import time
from r3.state import ROOT,Journal,policy,read_json,atomic_json

if __name__=='__main__':
    patch=read_json(ROOT/'BUYER_V12_POLICY_PATCH.json',{})
    assert patch and all(k.startswith('buyer_') for k in patch)
    j=Journal();before=policy()
    record={'at':time.time(),'before_fields':{k:before.get(k) for k in patch},'patch':patch,
        'runtime':read_json(j.md/'runtime.json',{}),'health':read_json(j.md/'shop-health.json',{}),
        'shop_strategy_revision':j.get('shop_strategy_revision'),'buyer_strategy_revision':j.get('buyer_strategy_revision'),
        'board':{k:[r for r in j.snapshot('board.json',{}).get(k,[]) if r.get('name')=='seochanit'] for k in ['shops','buyers']}}
    atomic_json(ROOT/'policy.json',dict(read_json(ROOT/'policy.json',{}),**patch))
    j.add('buyer_v12_deployment',record,role='buyer')
    path=ROOT/'logs'/('buyer-v12-deployment-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,record);print('SOURCE',path)
