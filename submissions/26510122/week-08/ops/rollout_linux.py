"""Freeze reviewed source and update Linux runtimes without clearing their state.

Seller restarts after the current supply window. Buyer keeps its kit and issued
order alive; only new per-order CLI processes load the new frozen release.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ops import buyer_service,seller_service


def run(*args):return subprocess.run(args,check=True,capture_output=True,text=True)
def read(path):return json.loads(path.read_text())
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def hashes(runtime):
    m=read(runtime/'release.json')
    return all(digest(runtime/'release'/name)==value for name,value in m['sha256'].items())


def rebound_seller_config(stage,runtime):
    config=read(stage/'agent.json')
    for key in ('seller','restock'):
        config[key]['cmd']=config[key]['cmd'].replace(str(stage/'release'),str(runtime/'release'))
    return config


def offline_gap(runtime,now,registry):
    """Explicit offline recovery requires stale data and an absent own listing."""
    path=runtime/'.market/inventory.json'
    if now-path.stat().st_mtime<90:return False
    shops=registry.get('shops') if isinstance(registry,dict) else None
    if not isinstance(shops,list):return False
    return not any(s.get('name')=='outseop-26510122' for s in shops)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--tag',required=True);parser.add_argument('--allow-offline',action='store_true',help='Allow state-preserving recovery only with stale inventory and no own registry entry');args=parser.parse_args()
    if sys.platform!='linux':raise RuntimeError('This rollout is for Linux user services')
    if not args.tag.replace('-','').isalnum():raise ValueError('Use an alphanumeric tag')
    commit=run('git','-C',str(ROOT),'rev-parse','HEAD').stdout.strip()
    runtimes={role:ROOT/'state'/f'{role}-runtime' for role in ('seller','buyer')}
    before={role:read(rt/'running.json') for role,rt in runtimes.items()}
    promotion=runtimes['seller']/'state/seller-opening-event.json'
    promo=digest(promotion)
    stages={role:ROOT/'state'/f'linux-stage-{args.tag}-{role}' for role in runtimes}
    seller_service.prepare(stages['seller'],runtimes['seller'])
    buyer_service.prepare(stages['buyer'],runtimes['buyer'])
    for role,stage in stages.items():
        assert hashes(stage) and read(stage/'release.json')['source_commit']==commit
    seller_config_before=(runtimes['seller']/'agent.json').read_bytes()
    seller_config_after=rebound_seller_config(stages['seller'],runtimes['seller'])
    # Never terminate a sequential supply batch in an open or imminent window.
    offline=False;offline_inventory_mtime=None
    deadline=time.monotonic()+360  # imminent-window buffer + 3-minute window + file refresh
    while True:
        path=runtimes['seller']/'.market/inventory.json';inv=read(path);now=time.time();window=inv.get('restock') or {}
        if now-path.stat().st_mtime<30 and not window.get('open') and window.get('opens_at',0)-now>90:break
        if args.allow_offline and now-path.stat().st_mtime>=90:
            observed_mtime=path.stat().st_mtime
            with urllib.request.urlopen('https://market.ourolabs.ai/api/shops',timeout=10) as response:registry=json.load(response)
            if offline_gap(runtimes['seller'],time.time(),registry) and path.stat().st_mtime==observed_mtime:
                offline=True;offline_inventory_mtime=observed_mtime;break

        if time.monotonic()>deadline:raise RuntimeError('No safe gap between supply windows; releases not changed')
        time.sleep(2)
    report={'at':time.time(),'source_commit':commit,'before':before,'promotion_hash_before':promo,'offline_recovery':offline}
    backups={};swapped=[];paused=False;stopped=False;pid=before['buyer']['kit_pid']
    try:
        run('systemctl','--user','stop','outseop-r3-seller.service');stopped=True
        if offline:
            path=runtimes['seller']/'.market/inventory.json'
            if path.stat().st_mtime!=offline_inventory_mtime or time.time()-path.stat().st_mtime<90:
                raise RuntimeError('Inventory recovered during offline guard; use a normal safe gap')

        os.kill(pid,signal.SIGSTOP);paused=True;time.sleep(2)
        for role,runtime in runtimes.items():
            backup=runtime/f'release-previous-{args.tag}'
            if backup.exists() or backup.with_suffix('.json').exists():raise FileExistsError(backup)
            (runtime/'release').rename(backup);(runtime/'release.json').rename(backup.with_suffix('.json'))
            backups[role]=backup
            (stages[role]/'release').rename(runtime/'release')
            (stages[role]/'release.json').rename(runtime/'release.json');swapped.append(role)
        config_backup=runtimes['seller']/f'release-previous-{args.tag}.agent.json'
        config_backup.write_bytes(seller_config_before)
        config_temp=runtimes['seller']/f'agent-{args.tag}.tmp'
        config_temp.write_text(json.dumps(seller_config_after,indent=2))
        os.replace(config_temp,runtimes['seller']/'agent.json')
        os.kill(pid,signal.SIGCONT);paused=False
        run('systemctl','--user','start','outseop-r3-seller.service');stopped=False
    except Exception:
        for role,backup in reversed(list(backups.items())):
            runtime=runtimes[role]
            if (runtime/'release').exists():(runtime/'release').rename(runtime/f'release-rejected-{args.tag}')
            if (runtime/'release.json').exists():(runtime/'release.json').rename(runtime/f'release-rejected-{args.tag}.json')
            backup.rename(runtime/'release');backup.with_suffix('.json').rename(runtime/'release.json')
        (runtimes['seller']/'agent.json').write_bytes(seller_config_before)
        raise
    finally:
        if paused:os.kill(pid,signal.SIGCONT)
        if stopped:run('systemctl','--user','start','outseop-r3-seller.service')
    # Verify both fixed files and actual per-order loading. The old buyer kit
    # continues unchanged, so its supervisor source commit can differ.
    for _ in range(70):
        after={role:read(rt/'running.json') for role,rt in runtimes.items()}
        report['after']=after
        if all(r.get('source_commit')==commit for r in after.values()):break
        time.sleep(1)
    buyer_config=read(runtimes['buyer']/'agent.json')
    buyer_config['harness']='Plain issued-order buyer; independent ledger-backed forum worker'
    (runtimes['buyer']/'agent.json').write_text(json.dumps(buyer_config,indent=2))
    report['hashes_match']={role:hashes(rt) for role,rt in runtimes.items()}
    report['promotion_hash_after']=digest(promotion)
    report['buyer_kit_preserved']=after['buyer']['kit_pid']==before['buyer']['kit_pid']
    report['buyer_supervisor_preserved']=after['buyer']['supervisor_pid']==before['buyer']['supervisor_pid']
    report['plain_buyer']=read(runtimes['buyer']/'agent.json').keys()=={'harness','market_buyer','review'}
    try:
        os.kill(after['seller']['procurement_pid'],0);report['procurement_worker_alive']=True
    except (KeyError,OSError):report['procurement_worker_alive']=False
    hook=read(runtimes['seller']/'agent.json')['restock']['cmd']
    report['prepared_restock_hook']='procurement_scheduler.py' in hook and '--execute' in hook
    report['public_card_status']=None;report['registry_matches']=False
    for _ in range(3 if offline else 20):
        try:
            current=read(runtimes['seller']/'running.json');url=current['url']
            with urllib.request.urlopen(url+'/.well-known/agent-card.json',timeout=8) as response:
                report['public_card_status']=response.status
            with urllib.request.urlopen('https://market.ourolabs.ai/api/shops',timeout=8) as response:
                shops=json.load(response)['shops']
            report['registry_matches']=any(s.get('name')=='outseop-26510122' and s.get('url')==url for s in shops)
            if report['public_card_status']==200 and report['registry_matches']:break
        except (OSError,ValueError):pass
        time.sleep(2)
    # The first inventory refresh can lag the ready public card after restart.
    # Observe it before deciding whether the release is unhealthy.
    inventory_deadline=time.monotonic()+(10 if offline else 60)
    while True:
        report['inventory_age_seconds']=time.time()-(runtimes['seller']/'.market/inventory.json').stat().st_mtime
        if -2<=report['inventory_age_seconds']<30 or time.monotonic()>=inventory_deadline:break
        time.sleep(2)
    # A real buyer order can load the new release during the network checks.
    after={role:read(rt/'running.json') for role,rt in runtimes.items()};report['after']=after
    report['complete']=all(report['hashes_match'].values()) and all(r.get('source_commit')==commit for r in after.values()) and report['buyer_kit_preserved'] and report['buyer_supervisor_preserved'] and report['plain_buyer'] and report['procurement_worker_alive'] and report['prepared_restock_hook'] and promo==report['promotion_hash_after'] and report['public_card_status']==200 and report['registry_matches'] and report['inventory_age_seconds']<30
    path=ROOT/'logs'/f'linux-rollout-{args.tag}.json';path.write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2),flush=True)
    if not report['complete']:raise RuntimeError('Rollout health incomplete; inspect the saved report before declaring completion')

if __name__=='__main__':main()
