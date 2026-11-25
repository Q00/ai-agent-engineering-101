"""One-shot release coordinator: await discovery recovery, then restart safely.

No strategy edits or trades. Only the existing identity-bound runtime is drained.
Two healthy observations are required; source changes cancel this release.
"""
from datetime import datetime,timezone
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import time
import httpx
from r3.state import ROOT,read_json,atomic_json
from r3.lifecycle import lease

REVISION='shop-v7-capital-velocity'


def probe(client=httpx,now=None):
    now=time.time() if now is None else now
    health=read_json(ROOT/'.market/shop-health.json',{})
    url=health.get('url','').rstrip('/')
    result={'ready':False,'at':now,'local_health':health.get('status')}
    try:
        response=client.get('https://market.ourolabs.ai/api/shops',timeout=8,trust_env=False)
        shops=response.json().get('shops',[]) if response.status_code==200 else []
        own=next((r for r in shops if r.get('name')=='seochanit'),{})
        result.update(registry_status=response.status_code,shop_count=len(shops),own_listed=bool(own))
        if len(shops)<3 or own and own.get('url','').rstrip('/')!=url:return result
        # Missing registration is what this recovery release must repair.
        # Requiring our old URL to be listed would deadlock after a market-side
        # timeout while otherwise healthy peer shops are already discoverable.
        result['requires_reregistration']=not bool(own)
        card=client.get(url+'/.well-known/agent-card.json',timeout=8,trust_env=False)
        result['card_status']=card.status_code
        result['ready']=(card.status_code==200 and health.get('status')=='healthy' and
                         0<=now-health.get('checked_at',0)<120)
    except Exception as exc:result['error']=type(exc).__name__
    return result


def fingerprint():
    files=[*sorted((ROOT/'r3').glob('*.py')),*[ROOT/name for name in
        ['policy.json','restock.py','seller_r3.py','market_buyer.py','run_market.py','watch_round3.py',
         'prepare_autonomous_restart.py','deploy_shop_when_ready.py']]]
    return hashlib.sha256(b''.join(p.name.encode()+p.read_bytes() for p in files)).hexdigest()


def main(test_log):
    text=test_log.read_bytes()
    text=text.decode('utf-16' if text.startswith(b'\xff\xfe') else 'utf-8-sig')
    if not re.search(r'\b\d+ passed\b',text) or re.search(r'\b\d+ failed\b',text):
        raise RuntimeError('A complete passing release test log is required')
    if read_json(ROOT/'policy.json',{}).get('shop_strategy_revision')!=REVISION:
        raise RuntimeError('Release revision does not match approved configuration')
    expected=fingerprint();status_path=ROOT/'.market/shop-rollout-status.json'
    def record(phase,**data):
        value={'at':time.time(),'phase':phase,'revision':REVISION,'source_sha256':expected,**data}
        atomic_json(status_path,value);print(json.dumps(value),flush=True)
    with lease(ROOT/'.market/shop-rollout.lock'):
        ready=0
        while True:
            if fingerprint()!=expected:
                record('cancelled_source_changed');return 1
            state=probe();ready=ready+1 if state['ready'] else 0
            record('waiting_for_market',consecutive_ready=ready,market=state)
            if ready>=2:break
            time.sleep(30)
        record('draining_existing_runtime')
        drained=subprocess.run([sys.executable,str(ROOT/'prepare_autonomous_restart.py')],
            cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=65)
        print(drained.stdout,flush=True)
        if drained.returncode:
            record('failed_to_drain');return 1
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        console=ROOT/'logs'/('shop-v7-watch-console-'+stamp+'.txt')
        error=ROOT/'logs'/('shop-v7-watch-errors-'+stamp+'.txt')
        with console.open('x') as out,error.open('x') as err:
            worker=subprocess.Popen([sys.executable,'-u',str(ROOT/'watch_round3.py')],cwd=ROOT,
                stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        record('started',watcher_pid=worker.pid,console=console.name,error_log=error.name)
        deadline=time.monotonic()+240
        while time.monotonic()<deadline:
            time.sleep(15)
            if not probe()['ready']:continue
            with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
                row=conn.execute('SELECT value FROM kv WHERE key=?',('shop_strategy_revision',)).fetchone()
            if not row or json.loads(row[0])!=REVISION:continue
            verified=subprocess.run([sys.executable,str(ROOT/'verify_autonomous_live.py')],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=35)
            print(verified.stdout,flush=True)
            if verified.returncode==0:
                record('verified',watcher_pid=worker.pid,console=console.name,error_log=error.name)
                return 0
        record('started_verification_pending',watcher_pid=worker.pid,
               console=console.name,error_log=error.name)
        return 0


if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Pass the complete release test log path')
    raise SystemExit(main(ROOT/sys.argv[1]))
