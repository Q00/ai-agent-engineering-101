"""Operator deployment via the official kit; reuse names, keys and in-memory claim."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import httpx

HERE = Path(__file__).resolve().parent
SUB = HERE.parents[1]
REPO = SUB.parents[2]
OLD_PID = 3469083
sys.path.insert(0, str(REPO / 'scripts'))
import market_kit


def emit(event, **fields):
    print(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': event, **fields}), flush=True)


def tail(path):
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size-64000))
        return stream.read().decode('utf-8', errors='replace').splitlines()


def children_buying(pid):
    path = Path(f'/proc/{pid}/task/{pid}/children')
    if not path.exists():
        return []
    active = []
    for raw in path.read_text().split():
        try:
            cmd = Path(f'/proc/{raw}/cmdline').read_bytes().decode().replace('\0',' ')
            if 'market_buyer.py' in cmd or 'buyer.py --url' in cmd:
                active.append(int(raw))
        except FileNotFoundError:
            pass
    return active


url = json.loads((HERE/'tunnel-start.json').read_text())['url']
cmd = Path(f'/proc/{OLD_PID}/cmdline').read_bytes().decode()
assert 'market_kit.py' in cmd and str(SUB) in cmd and 'myeonggi' in cmd
old_env = dict(row.split(b'=',1) for row in Path(f'/proc/{OLD_PID}/environ').read_bytes().split(b'\0') if b'=' in row)
claim = old_env.get(b'MARKET_OWNER')
assert claim, 'Existing ownership claim missing; do not create an unowned profile'
for key in ('shop_key.pem','buyer_key.pem'):
    assert (SUB/key).is_file(), 'Existing identity key must not be regenerated'
key_hashes = {name:hashlib.sha256((SUB/name).read_bytes()).hexdigest() for name in ('shop_key.pem','buyer_key.pem')}
shop_key = market_kit.load_key(SUB/'shop_key.pem')
public = httpx.get(url+'/.well-known/agent-card.json',timeout=15,trust_env=False)
public.raise_for_status()
assert public.json()['description'] == json.loads((HERE/'card-before.json').read_text())['description']
shops = httpx.get('https://market.ourolabs.ai/api/shops',timeout=15).json()['shops']
existing = next(row for row in shops if row['name']=='myeonggi')
assert existing['pubkey'] == market_kit.pub_b64(shop_key)
market = market_kit.Market('https://market.ourolabs.ai',claim.decode())
# Publish the ready new URL first; leave the old seller alive to drain existing callers.
joined = market.join('shop','myeonggi',shop_key,url)
assert joined['name']=='myeonggi'
published = datetime.now(timezone.utc).isoformat()
emit('existing_shop_updated',name=joined['name'],url=url,keys_reused=True,owner_claim_reused=True)
for _ in range(6):
    time.sleep(30)
    emit('old_endpoint_draining',old_kit_pid=OLD_PID,active_buyer_children=children_buying(OLD_PID))
# Complete the old kit's current order before stopping its polling loop.
console = SUB/'logs/shop-description-owner-20261021T121248Z/market-console.log'
deadline = time.monotonic()+240
while time.monotonic()<deadline:
    lines=tail(console)
    order=max((i for i,line in enumerate(lines) if '[order ' in line),default=-1)
    finished=max((i for i,line in enumerate(lines) if '[market] ledger #' in line or '[kit] order failed:' in line),default=-1)
    if not children_buying(OLD_PID) and finished>order:
        break
    time.sleep(.2)
else:
    raise RuntimeError('Old buyer still active; keeping old kit rather than interrupting a possible purchase')
old_children=[int(x) for x in Path(f'/proc/{OLD_PID}/task/{OLD_PID}/children').read_text().split()]
os.kill(OLD_PID,signal.SIGTERM)
for _ in range(300):
    proc=Path(f'/proc/{OLD_PID}/stat')
    if not proc.exists() or proc.read_text().split(') ')[1].split()[0]=='Z':
        break
    time.sleep(.1)
else:
    raise RuntimeError('Old kit did not stop; refusing duplicate buyer workers')
env=os.environ.copy()
env['MARKET_OWNER']=claim.decode()
env['PATH']=str(REPO/'.venv/bin')+os.pathsep+env.get('PATH','')
command=[str(REPO/'.venv/bin/python'),'-u',str(REPO/'scripts/market_kit.py'),'join','--dir',str(SUB),'--market','https://market.ourolabs.ai','--name','myeonggi','--deployed',url]
with (HERE/'market-final-console.log').open('a') as output:
    new=subprocess.Popen(command,cwd=REPO,env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
result={'shop_published_at':published,'kit_restarted_at':datetime.now(timezone.utc).isoformat(),
        'old_kit_pid':OLD_PID,'new_kit_pid':new.pid,'public_url':url,'registry_name':'myeonggi',
        'owner_claim_reused_in_memory':True,'identity_file_sha256':key_hashes,'old_children_before_stop':old_children,
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'independent_tunnel_pid':json.loads((HERE/'tunnel-start.json').read_text())['pid']}
(HERE/'cutover-complete.json').write_text(json.dumps(result,indent=2))
(SUB/'.market/deployment-runtime.json').write_text(json.dumps(result,indent=2))
emit('new_kit_started',pid=new.pid,url=url,name='myeonggi',existing_keys_reused=True)
