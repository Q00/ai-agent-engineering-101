"""Read-only SETUP 3 / R3 readiness receipt; never requests an order or payment."""
import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import httpx

HERE=Path(__file__).resolve().parent
AGENT=HERE.parent/'week-07'
REPO=HERE.parents[2]
sys.path.insert(0,str(AGENT))
from negotiation.experience import Experience


def alive(pid):
    kernel=ctypes.windll.kernel32
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        status=ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(status)) and status.value==259)
    finally:kernel.CloseHandle(handle)


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    result={'at_utc':stamp,'checks':{},'roles':{}}
    checks=result['checks']
    checks['kit_v3']=bool(re.search(r'KIT_VERSION\s*=\s*"3"',(REPO/'scripts/market_kit.py').read_text(encoding='utf-8')))
    for role,file in [('buyer','session.json'),('shop','shop-session.json')]:
        state=json.loads((AGENT/'.market'/file).read_text(encoding='utf-8-sig'))
        text=Path(state['stdout']).read_text(encoding='utf-8')
        checks[role+'_process_alive']=alive(state['pid'])
        checks[role+'_session_v3']='[kit] market_kit 3,' in text
        checks[role+'_registered']=f'[market] {role} registered as seochanit,' in text
        result['roles'][role]={'pid':state['pid'],'session_log':str(Path(state['stdout']).relative_to(REPO))}
        if role=='shop':
            urls=re.findall(r'\[shop\].*?at (https://\S+)',text)
            result['shop_url']=urls[-1] if urls else None
    with httpx.Client(timeout=20) as client:
        registry=client.get('https://market.ourolabs.ai/api/shops');registry.raise_for_status();registry=registry.json()
        result['market_round']=registry['round']
        checks['registered_shop_visible']=any(x['name']=='seochanit' for x in registry['shops'])
        url=result.get('shop_url')
        if url:
            response=client.get(url+'/.well-known/acp.json')
            checks['public_acp_http_200']=response.status_code==200
            descriptor=response.json() if response.status_code==200 else {}
            checks['acp_ap2_mandate']='ap2-mandate' in descriptor.get('capabilities',{}).get('payment_methods',[])
            card=client.get(url+'/.well-known/agent-card.json');card.raise_for_status()
            checks['updated_shop_card']=card.json().get('version')=='1.1.0'
            result['acp']=descriptor
    result['journal_events']=Experience().status()
    result['journal_round']=Experience().current_round()
    checks['persistent_journal_available']=bool(result['journal_events'])
    result['ready']=all(checks.values()) and bool(result.get('shop_url'))
    result['r3_live_settlement']='not yet verified' if result['market_round']<3 else 'inspect LAB payment evidence'
    output=HERE/'logs'/('readiness-'+stamp+'.json')
    with output.open('x',encoding='utf-8') as f:f.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if result['ready'] else 1


if __name__=='__main__':raise SystemExit(main())
