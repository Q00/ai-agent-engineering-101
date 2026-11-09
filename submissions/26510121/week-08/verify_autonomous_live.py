"""Public GET verification of registration plus read-only local controller evidence."""
import concurrent.futures
from datetime import datetime,timezone
import json
import sys
import httpx
from r3.state import ROOT,read_json,policy
from autonomous_status import status


def verify():
    local=status()
    url=local['health'].get('url','').rstrip('/')
    paths=[url+'/.well-known/agent-card.json',url+'/.well-known/acp.json',
           'https://market.ourolabs.ai/api/board']
    def get(path):
        try:
            response=httpx.get(path,timeout=12,trust_env=False)
            data=response.json() if response.status_code==200 else {}
            if path.endswith('/api/board'):
                data={'round':data.get('round'),**{k:[{f:r.get(f) for f in
                    ['name','alive','all','deals','revenue','profit','book','stock_units','surplus','saved','forum','posts','served']}
                    for r in data.get(k,[]) if r.get('name')=='seochanit'] for k in ['shops','buyers']}}
            else:data={k:data.get(k) for k in ['name','protocol']}
            return {'url':path,'status':response.status_code,'data':data}
        except Exception as exc:return {'url':path,'error_type':type(exc).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:public=list(pool.map(get,paths))
    good=all(r.get('status')==200 for r in public)
    board=public[2].get('data',{})
    good=good and all(board.get(k) and board[k][0].get('alive') for k in ['shops','buyers'])
    good=good and local['objective_revision']==local['policy_version'] and local['control'].get('status')=='observe_and_trade'
    if local.get('buyer_patch34_enabled'):
        good=good and local.get('buyer_strategy_revision')==local.get('buyer_expected_revision')
        expected=policy().get('buyer_forum_metric')
        if expected:good=good and local.get('rules',{}).get('buyer_forum_metric')==expected
    if local.get('shop_championship_enabled'):
        good=good and local.get('shop_strategy_revision')==local.get('shop_expected_revision')
        progress=local.get('championship_progress',{})
        good=good and progress.get('scope','').startswith('everyone') and bool(progress.get('next_thresholds'))
    result={'verified':bool(good),'public':public,'local':local}
    path=ROOT/'logs'/('autonomous-verified-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print(json.dumps({'verified':bool(good),'public':public,'counts':local['counts_since_epoch'],
                      'controller':local['control'].get('status')},ensure_ascii=False))
    return good


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(0 if verify() else 1)
