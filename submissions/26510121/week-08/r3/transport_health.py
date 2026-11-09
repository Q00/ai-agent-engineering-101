"""Independent positive evidence when the primary health heartbeat is stale."""
from concurrent.futures import ThreadPoolExecutor
import time
from urllib.parse import urlsplit
import httpx
from .state import atomic_json,read_json


def refresh(journal,own,now=None):
    now=time.time() if now is None else now
    url=(own.get('card') or {}).get('url','').rstrip('/')
    parts=urlsplit(url)
    if (own.get('alive') is not True or own.get('name')!='seochanit' or
            parts.scheme!='https' or not parts.hostname or parts.username or parts.password):
        return None
    runtime=journal.snapshot('runtime.json',{})
    def get(path):
        try:
            r=httpx.get(path,timeout=6,trust_env=False,follow_redirects=False)
            return {'status':r.status_code,'data':r.json() if r.status_code==200 else {}}
        except Exception as exc:return {'error':type(exc).__name__,'data':{}}
    paths=[url+'/.well-known/agent-card.json',url+'/.well-known/acp.json',
           'https://market.ourolabs.ai/api/shops']
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(get,paths))
    name=(own.get('owner') or {}).get('login')
    present=any(r.get('name')=='seochanit' and r.get('url','').rstrip('/')==url and
                (not name or (r.get('owner') or {}).get('login')==name)
                for r in rows[2]['data'].get('shops',[]))
    good=(all(r.get('status')==200 for r in rows) and present and
          rows[0]['data'].get('name')=='Seochanit Shop' and rows[1]['data'].get('protocol')=='acp' and
          journal.snapshot('runtime.json',{})==runtime)
    journal.add('shop_health_independent_probe',{'at':now,'url':url,'healthy':good,
                 'statuses':[r.get('status') for r in rows],'registry_present':present})
    if not good:return None
    previous=journal.snapshot('shop-health.json',{})
    snapshot={'checked_at':now,'status':'healthy','url':url,'pending_registration':False,
              'consecutive_probe_failures':0,'market_visibility':'present','registry_misses':0,
              'probe_interval_seconds':15,'renewal_seconds':previous.get('renewal_seconds'),
              'source':'independent_card_acp_registry_probe',
              'primary_heartbeat_at':previous.get('primary_heartbeat_at',previous.get('checked_at'))}
    atomic_json(journal.md/'shop-health.json',snapshot)
    return snapshot
