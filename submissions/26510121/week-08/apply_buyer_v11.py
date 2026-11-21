"""Hot-apply a tested Buyer-only policy; preserve live Shop process and identity."""
from datetime import datetime,timezone
import json
import time
from r3.state import ROOT,Journal,read_json,policy,atomic_json


def main():
    patch=read_json(ROOT/'BUYER_V11_POLICY_PATCH.json',{})
    assert set(patch)=={'buyer_strategy_revision','buyer_market_quality_enabled',
                        'buyer_contract_routing_enabled','buyer_goods_time_seconds','buyer_negotiation_turns'}
    assert patch['buyer_market_quality_enabled'] and patch['buyer_contract_routing_enabled']
    j=Journal();before=policy();at=time.time()
    record={'at':at,'mode':'buyer child subprocess hot apply, no process restart',
            'runtime':read_json(j.md/'runtime.json',{}),'health':read_json(j.md/'shop-health.json',{}),
            'shop_strategy_revision':j.get('shop_strategy_revision'),
            'buyer_strategy_revision':j.get('buyer_strategy_revision'),
            'previous_fields':{k:before.get(k) for k in patch},'patch':patch,
            'board':{key:[r for r in j.snapshot('board.json',{}).get(key,[]) if r.get('name')=='seochanit']
                     for key in ['shops','buyers']}}
    atomic_json(ROOT/'policy.json',dict(read_json(ROOT/'policy.json',{}),**patch))
    j.add('buyer_v11_deployment',record,role='buyer')
    path=ROOT/'logs'/('buyer-v11-deployment-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,record)
    print('SOURCE',path);print('Applied Buyer-only policy. Next new order activates its revision.')


if __name__=='__main__':main()
