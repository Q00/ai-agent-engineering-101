"""Typed, expiring observer-to-supervisor requests; one mutation owner."""
from .state import atomic_json, identity, read_json


def request(directory, runtime, url, evidence, now):
    required=('runtime_matches','verified','identity_matches','public_ready')
    if (not runtime.get('session') or not all(evidence.get(k) is True for k in required)
            or not evidence.get('active_peers') or not evidence.get('registry_count')
            or evidence.get('shop_alive') not in (True,False)):
        return None
    row={'schema':1,'session':runtime['session'],'url':url.rstrip('/'),'action':'rejoin',
         'at':now,'expires_at':now+90,'source':'independent_recovery_observer',
         'evidence':{k:evidence.get(k) for k in (*required,'active_peers','registry_count','listed','shop_alive')}}
    row['id']=identity(row)
    atomic_json(directory/'recovery-request.json',row)
    return row['id']


def receive(directory, runtime, url, now, previous_id=None):
    row=read_json(directory/'recovery-request.json',{})
    if (row.get('schema')!=1 or row.get('session')!=runtime.get('session') or not runtime.get('session')
            or row.get('url')!=url.rstrip('/') or row.get('action')!='rejoin'
            or row.get('source')!='independent_recovery_observer' or row.get('id')==previous_id
            or not isinstance(row.get('at'),(int,float)) or not isinstance(row.get('expires_at'),(int,float))
            or not 0<=now-row['at']<90 or now>=row['expires_at']
            or row.get('id')!=identity({k:v for k,v in row.items() if k!='id'})):
        return None
    e=row.get('evidence',{})
    if (not all(e.get(k) is True for k in ('runtime_matches','verified','identity_matches','public_ready'))
            or not e.get('active_peers') or not e.get('registry_count')
            or e.get('shop_alive') not in (True,False)):
        return None
    return row
