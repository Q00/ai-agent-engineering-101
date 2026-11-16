"""Seller-only service with a frozen release and renewable public tunnel.

Prepare once from reviewed source. Running the service never reloads unfinished
buyer work. Private keys stay in their existing location through local symlinks.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
FILES=('seller_checkout.py','buyer_breakage.py','seller_r3.py','harness.py','market_memory.py','market_scoring.py','decision_audit.py','newsvendor_tool.py','context_compaction.py',
       'inventory_observation.py','inventory_exploration.py','inventory_portfolio.py','procurement_strategy.py','procurement_audit.py','procurement_scheduler.py','procurement_transport.py','procurement_monitor.py','seller_kit.py','supply_reconciliation.py','stock_policy.py',
       'seller_state.py','seller_quality.py','seller_requirements.py','seller_warranty.py','seller_demand.py','seller_featured.py','replenishment_plan.py','restock.py','buyer_identity.py','counterparty_memory.py','seller_forum.py','seller_promotion.py','capital_policy.py','procurement_cost.py','seller_meta_policy.py','seller_meta.py','meta_effects.py','buyer_forum.py')
STOP=False


def log(message):print(time.strftime('%Y-%m-%d %H:%M:%S')+' '+message,flush=True)


def kit_entrypoint(runtime,kit,manifest):
    # Source can update before a frozen release is swapped, or during rollback.
    # Only a manifest-verified new release may use the observing entrypoint.
    return ([str(runtime/'release/seller_kit.py'),str(kit)] if 'seller_kit.py' in manifest['sha256'] else [str(kit)])


def prepare(runtime,keys):
    runtime.mkdir(parents=True,exist_ok=True)
    release=runtime/'release';release.mkdir(exist_ok=False)
    digests={}
    for name in FILES:
        shutil.copy2(ROOT/name,release/name)
        digests[name]=hashlib.sha256((release/name).read_bytes()).hexdigest()
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    if not (runtime/'logs').exists():(runtime/'logs').symlink_to(logs,target_is_directory=True)
    for name in ('shop_key.pem','buyer_key.pem'):
        source=(keys/name).resolve()
        if not source.is_file():raise FileNotFoundError(f'Existing market key is missing: {name}')
        dest=runtime/name
        if not dest.exists():dest.symlink_to(source)
        elif dest.resolve()!=source:raise ValueError('Runtime has a different key source')
    python=ROOT/'.venv/bin/python'
    config={'harness':'Frozen seller-only Round 3 release; buyer and roleplay are not enabled',
            'seller':{'cmd':shlex.join([str(python),str(release/'seller_r3.py'),'--port','{port}'])},
            'restock':{'cmd':shlex.join([str(python),str(release/'procurement_scheduler.py'),'--execute','--state','{state}'])}}
    (runtime/'agent.json').write_text(json.dumps(config,indent=2))
    commit=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
    (runtime/'release.json').write_text(json.dumps({'source_commit':commit,'sha256':digests,'prepared_at':time.time()},indent=2))
    log(f'Prepared frozen seller-only release: {runtime}')


def stop(proc):
    if proc is None or proc.poll() is not None:return
    try:os.killpg(proc.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        proc.wait(timeout=5)


def tunnel_url(urls, alternate=False):
    """Choose only a provider URL, rotating domains after a failed kit start."""
    from urllib.parse import urlsplit
    suffixes=('.free.pinggy.net','.run.pinggy-free.link')
    if alternate:suffixes=suffixes[::-1]
    for suffix in suffixes:
        for url in urls:
            if not isinstance(url,str):continue
            parsed=urlsplit(url)
            if (parsed.scheme=='https' and parsed.hostname and parsed.hostname.endswith(suffix)
                    and not parsed.username and not parsed.password and parsed.path in ('','/')
                    and not parsed.query and not parsed.fragment):return url.rstrip('/')
    return None


def cloudflare_url(text):
    match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com(?=[\s|]|$)',text)
    return match[0] if match else None


def cloudflare_command(binary,port):
    # Forward the existing kit API proxy only, never a directory/file server.
    return [binary,'tunnel','--no-autoupdate','--protocol','http2','--url',f'http://127.0.0.1:{port}']


def tunnel_retry(provider, output, failures):
    if provider == 'cloudflare' and re.search(r'provisioning failed with status 429\b', output):
        return 'pinggy', 15
    return provider, min(300, 15 * 2 ** min(max(failures - 1, 0), 5))


def registry_matches(url):
    try:
        with urllib.request.urlopen('https://market.ourolabs.ai/api/shops', timeout=4) as response:
            data = json.load(response)
        shops = data.get('shops') if isinstance(data, dict) else None
        if not isinstance(shops, list) or any(
                not isinstance(s, dict) or not isinstance(s.get('name'), str)
                or not isinstance(s.get('url'), str) for s in shops):
            return None
        return any(s.get('name') == 'outseop-26510122' and s.get('url') == url for s in shops)
    except (OSError, ValueError):
        return None


def serving_recent_buyers(runtime, url, now):
    """A stale registry address alone cannot override fresh private trade evidence."""
    inventory=runtime/'.market/inventory.json'
    try:
        if not 0<=now-inventory.stat().st_mtime<30:
            return False
        with closing(sqlite3.connect(
                (runtime/'state/seller.sqlite3').resolve().as_uri()+'?mode=ro',
                uri=True,timeout=.5)) as db:
            at=db.execute('SELECT MAX(updated_at) FROM purchase_contexts').fetchone()[0]
        if type(at) not in (int,float) or not 0<=now-at<=180:
            return False
        with urllib.request.urlopen(url+'/.well-known/agent-card.json',timeout=3) as response:
            card=json.load(response)
        return (isinstance(card,dict) and card.get('name')=='outseop-26510122'
                and isinstance(card.get('supportedInterfaces'),list)
                and bool(card['supportedInterfaces']))
    except (OSError,ValueError,TypeError,sqlite3.Error):
        return False


def safe_tunnel_renewal(runtime, now):
    path = runtime / '.market/inventory.json'
    try:
        inventory = json.loads(path.read_text())
        if not isinstance(inventory, dict):
            return False
        window = inventory.get('restock') or {}
        if not isinstance(window, dict):
            return False
        return (0 <= now - path.stat().st_mtime < 30 and inventory.get('round') == 3
                and not window.get('open') and window.get('opens_at', 0) - now > 90)
    except (OSError, ValueError, TypeError):
        return False


def run(args):
    global STOP
    def finish(*_):
        global STOP
        STOP=True
    signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
    runtime=args.runtime
    manifest=json.loads((runtime/'release.json').read_text())
    for name,digest in manifest['sha256'].items():
        if hashlib.sha256((runtime/'release'/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Frozen release integrity changed')
    config=json.loads((runtime/'agent.json').read_text())
    if set(config)-{'harness','seller','restock'}:raise ValueError('Seller-only runtime contains buyer hooks')
    if not args.owner_file.is_file():raise FileNotFoundError('Existing market owner credential is missing')
    kit=ROOT.parents[2]/'scripts/market_kit.py'
    env=os.environ.copy()
    env['PATH']=f"{Path.home()/'.local/bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH','')}"
    env['MARKET_OWNER']=args.owner_file.read_text().strip()
    env['PYTHONUNBUFFERED']='1'
    alternate_domain=False
    provider=getattr(args,'tunnel_provider','pinggy')
    if provider not in ('pinggy','cloudflare'):raise ValueError('Unknown tunnel provider')
    failures=0
    retry_seconds=15
    while not STOP:
        ssh=market=forum=None;workers={}
        try:
            with (ROOT/'logs/seller-meta-console.log').open('a') as meta_log, \
                 (ROOT/'logs/procurement-console.log').open('a') as procurement_log, \
                 (ROOT/'state/buyer-runtime/logs/forum-console.log').open('a') as buyer_forum_log, \
                 (ROOT/'logs/seller-r3-tunnel.log').open('a') as ssh_log, \
                 (ROOT/'logs/seller-r3-kit-console.log').open('a') as kit_log, \
                 (ROOT/'logs/seller-forum-console.log').open('a') as forum_log:
                output_offset=ssh_log.tell()
                if provider=='cloudflare':
                    binary=shutil.which('cloudflared',path=env['PATH'])
                    if not binary:raise RuntimeError('Install verified cloudflared before selecting this provider')
                    command=cloudflare_command(binary,args.port)
                else:
                    command=['/usr/bin/ssh','-p','443','-tt','-o','BatchMode=yes','-o','ConnectTimeout=8',
                        '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3',
                        '-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={runtime/"known_hosts"}',
                        f'-R0:127.0.0.1:{args.port}',f'-L{args.debug_port}:localhost:4300','free.pinggy.io']
                ssh=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=ssh_log,stderr=subprocess.STDOUT,start_new_session=True)
                until=time.monotonic()+60;url=None
                while not STOP and ssh.poll() is None and time.monotonic()<until:
                    try:
                        if provider=='cloudflare':
                            with (ROOT/'logs/seller-r3-tunnel.log').open() as reader:
                                reader.seek(output_offset);url=cloudflare_url(reader.read(65536))
                        else:
                            with urllib.request.urlopen(f'http://127.0.0.1:{args.debug_port}/urls',timeout=2) as response:
                                urls=json.load(response).get('urls',[])
                            url=tunnel_url(urls,alternate_domain)
                        if url:break
                    except (OSError,ValueError):pass
                    time.sleep(1)
                if STOP:break
                if not url:raise RuntimeError('Public tunnel did not become ready')
                failures=0
                retry_seconds=15
                log(f'Tunnel ready: {url}')
                market=subprocess.Popen([str(ROOT/'.venv/bin/python'),*kit_entrypoint(runtime,kit,manifest),'join','--dir',str(runtime),
                    '--market','https://market.ourolabs.ai','--name','outseop-26510122','--port',str(args.port),
                    '--public-url',url,'--shop-only'],cwd=runtime,env=env,
                    stdout=kit_log,stderr=subprocess.STDOUT,start_new_session=True)
                # Separate process: copy generation never occupies the restock window.
                forum=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(runtime/'release/seller_forum.py'),
                    '--runtime',str(runtime),'--loop'],cwd=runtime,env={k:v for k,v in env.items() if k!='MARKET_OWNER'},
                    stdout=forum_log,stderr=subprocess.STDOUT,start_new_session=True)
                worker_specs={
                    'meta_pid':('seller_meta.py',runtime,meta_log),
                    'procurement_pid':('procurement_scheduler.py',runtime,procurement_log),
                    'buyer_forum_pid':('buyer_forum.py',ROOT/'state/buyer-runtime',buyer_forum_log)}
                def worker(spec):
                    script,working,output=spec
                    return subprocess.Popen([str(ROOT/'.venv/bin/python'),str(runtime/'release'/script),
                        '--runtime',str(working),'--loop'],cwd=working,
                        env={k:v for k,v in env.items() if k!='MARKET_OWNER'},
                        stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
                workers={key:worker(spec) for key,spec in worker_specs.items()}
                status={'supervisor_pid':os.getpid(),'kit_pid':market.pid,'tunnel_pid':ssh.pid,'forum_pid':forum.pid,'url':url,
                        'source_commit':manifest['source_commit'],'started_at':time.time(),'buyer_enabled':False,'tunnel_provider':provider}
                status.update({key:proc.pid for key,proc in workers.items()})
                (runtime/'running.json').write_text(json.dumps(status,indent=2))
                until=time.monotonic()+55*60 if provider=='pinggy' else float('inf')
                next_health=time.monotonic()+120
                missing_checks=0
                while not STOP and ssh.poll() is None and market.poll() is None:
                    clock=time.monotonic()
                    if clock>=next_health:
                        registered=registry_matches(url)
                        active=registered is False and serving_recent_buyers(runtime,url,time.time())
                        if active:
                            log('Registry address differs, but fresh buyer activity and public card are healthy; keeping tunnel')
                        missing_checks=missing_checks+1 if registered is False and not active else 0
                        next_health=time.monotonic()+60
                    if (clock>=until or missing_checks>=2) and safe_tunnel_renewal(runtime,time.time()):
                        if missing_checks>=2:
                            log('Shop absent from registry twice; renewing tunnel in a safe supply gap')
                        break
                    if forum.poll() is not None:
                        log('Forum worker exited; restarting independently')
                        forum=subprocess.Popen([str(ROOT/'.venv/bin/python'),str(runtime/'release/seller_forum.py'),
                            '--runtime',str(runtime),'--loop'],cwd=runtime,env={k:v for k,v in env.items() if k!='MARKET_OWNER'},
                            stdout=forum_log,stderr=subprocess.STDOUT,start_new_session=True)
                        status['forum_pid']=forum.pid
                        (runtime/'running.json').write_text(json.dumps(status,indent=2))
                    for key,proc in list(workers.items()):
                        if proc.poll() is not None:
                            log(f'{key} worker exited; restarting independently')
                            workers[key]=worker(worker_specs[key]);status[key]=workers[key].pid
                            (runtime/'running.json').write_text(json.dumps(status,indent=2))
                    time.sleep(2)
                log(f'Renew/exit: tunnel={ssh.poll()}, kit={market.poll()}')
                if market.poll() not in (None,0):
                    alternate_domain=not alternate_domain
                    log('Kit failed; next tunnel cycle will try the other provider domain first')
        except Exception as error:
            failures+=1
            output=''
            if provider=='cloudflare':
                try:
                    with (ROOT/'logs/seller-r3-tunnel.log').open() as reader:
                        reader.seek(output_offset)
                        output=reader.read(16384)
                except (OSError,UnboundLocalError):
                    pass
            next_provider,retry_seconds=tunnel_retry(provider,output,failures)
            log(f'Service cycle failed: {type(error).__name__}: {error}; retry in {retry_seconds}s')
            if next_provider!=provider:
                log('Cloudflare tunnel provisioning is rate limited; using the existing Pinggy fallback')
            provider=next_provider
        finally:
            for proc in workers.values():stop(proc)
            stop(forum);stop(market);stop(ssh)
        for _ in range(retry_seconds):
            if STOP:break
            time.sleep(1)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,default=ROOT/'state/seller-runtime')
    parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--keys-dir',type=Path)
    parser.add_argument('--owner-file',type=Path,default=Path.home()/'.config/ourolabs-market/owner-code')
    parser.add_argument('--tunnel-provider',choices=('pinggy','cloudflare'),default=os.environ.get('OUTSEOP_TUNNEL_PROVIDER','pinggy'))
    parser.add_argument('--port',type=int,default=9300);parser.add_argument('--debug-port',type=int,default=9437)
    args=parser.parse_args();args.runtime=args.runtime.resolve()
    if args.prepare_only:
        if not args.keys_dir:parser.error('--keys-dir required for preparation')
        prepare(args.runtime,args.keys_dir)
    else:run(args)
