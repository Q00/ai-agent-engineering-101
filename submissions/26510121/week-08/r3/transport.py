"""Reuse the verified week-07 transport path for v4, without editing the kit."""
from datetime import datetime, timezone
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import httpx
from .state import ROOT, atomic_json, read_json


def open_available_tunnel(kit,port,avoid=None):
    preferred=os.environ.get('MARKET_TUNNEL_PROVIDER','pinggy')
    previous=read_json(ROOT/'.market/transport-provider.json',{})
    if previous.get('provider') in {'pinggy','cloudflare','localhost'} and 0<=time.time()-previous.get('at',0)<86400:
        preferred=previous['provider']
    providers=list(dict.fromkeys([preferred,'cloudflare','localhost','pinggy']))
    if avoid in providers:providers.remove(avoid);providers.append(avoid)
    pinned=read_json(ROOT/'policy.json',{}).get('tunnel_provider')
    if pinned in {'cloudflare','localhost','pinggy'}:
        providers=[pinned]
    for provider in providers:
        try:
            process,url=open_tunnel(kit,port,provider)
            process.r3_provider=provider
            atomic_json(ROOT/'.market/transport-provider.json',{'at':time.time(),'provider':provider})
            return process,url
        except Exception as exc:
            kit.log('[tunnel] '+provider+' unavailable ('+type(exc).__name__+'); trying alternative transport')
    raise RuntimeError('Public tunnel providers failed; watcher will retry')


def open_tunnel(kit, port, provider=None, log_path=None, protocol='http2'):
    provider = provider or os.environ.get('MARKET_TUNNEL_PROVIDER', 'pinggy')
    card = httpx.get(f'http://127.0.0.1:{port}/.well-known/agent-card.json', timeout=5)
    card.raise_for_status()
    if card.json().get('name') != 'Seochanit Shop':
        raise RuntimeError('Refusing to expose an unexpected local service')
    if provider == 'pinggy':
        command = [sys.executable, '-u', str(ROOT / 'r3/pinggy_tunnel.py'), '--port', str(port)]
        pattern = r'TUNNEL_URL (https://[^\s]+)'
    elif provider == 'cloudflare':
        binary = ROOT / '.market/bin/cloudflared.exe'
        command = [str(binary) if binary.is_file() else shutil.which('cloudflared') or 'cloudflared',
                   'tunnel', '--no-autoupdate', '--url', f'http://127.0.0.1:{port}',
                   '--protocol', protocol, '--edge-ip-version', '4']
        pattern = r'https://[a-z0-9-]+\.trycloudflare\.com'
    elif provider == 'localhost':
        command=[shutil.which('ssh') or 'ssh','-T','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','IdentityAgent=none','-o','IdentityFile=none','-o','StrictHostKeyChecking=accept-new',
            '-o','UserKnownHostsFile='+str(ROOT/'.market/ssh-known-hosts'),
            '-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10',
            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
            '-R',f'80:127.0.0.1:{port}','nokey@localhost.run','--','--output','json']
        # The SSH welcome banner links admin.localhost.run before the actual
        # forwarding address. Never use that account portal as our shop URL.
        pattern=r'https://(?!admin\.localhost\.run\b)[a-z0-9-]+\.(?:lhr\.life|localhost\.run)'
    else:
        raise ValueError('Unsupported tunnel provider')
    if log_path is None:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        log_path = ROOT / 'logs' / f'tunnel-{provider}-{stamp}.txt'
    # Reserve the append-only log before launching a subprocess.
    out = log_path.open('x', encoding='utf-8')
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {'OPENAI_API_KEY', 'MARKET_OWNER'}}
    try:
        proc = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', env=env,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except BaseException:
        out.close()
        raise
    found = []

    def capture():
        try:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                match = re.search(pattern, line)
                if match and not found:
                    found.append(match.group(1) if provider == 'pinggy' else match.group())
        finally:
            out.close()
            proc.stdout.close()

    reader = threading.Thread(target=capture, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and proc.poll() is None:
            if found:
                return proc, found[0]
            time.sleep(.2)
        raise RuntimeError(f'{provider} did not provision a live tunnel; see {log_path.name}')
    except BaseException:
        kit.stop(proc)
        reader.join(timeout=5)
        raise


class ShopSupervisor:
    """Registration is independent of reachability; prepare a replacement before expiry."""
    def __init__(self, kit, args, agent, directory, market, keys, procs, proxy, sport, pport, state,
                 now=None, provider=None):
        self.kit, self.args, self.agent, self.directory = kit, args, agent, directory
        self.market, self.keys, self.procs, self.proxy = market, keys, procs, proxy
        self.sport, self.pport, self.state = sport, pport, state
        self.provider = provider or getattr(procs[1],'r3_provider',None) or os.environ.get('MARKET_TUNNEL_PROVIDER', 'pinggy')
        self.started = time.monotonic() if now is None else now
        self.misses = 0
        self.pending_registration = False
        self.retired = None
        self.registry_misses=0
        self.registration_failures=0
        self.last_registry_probe=-1000
        self.market_visibility='unknown'

    def snapshot(self, status):
        atomic_json(self.directory / '.market/shop-health.json', {
            'checked_at': time.time(), 'status': status, 'url': self.proxy.public,
            'pending_registration': self.pending_registration, 'consecutive_probe_failures': self.misses,
            'market_visibility':self.market_visibility,'registry_misses':self.registry_misses,
            'probe_interval_seconds': 15, 'renewal_seconds': 3300 if self.provider == 'pinggy' else None})

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        kit = self.kit
        if self.procs[0].poll() is not None:
            kit.log('[kit] seller exited; restarting its existing command')
            self.procs[0] = kit.start(kit.fill(self.agent['seller']['cmd'], port=self.sport), self.directory)
            kit.pump(self.procs[0], '[seller]')
            if not kit.wait_card(f'http://127.0.0.1:{self.sport}', 30):
                self.snapshot('seller_starting')
                return
        if not self.args.tunnel:
            return
        healthy = self.procs[1].poll() is None and kit.card_ok(self.proxy.public)
        self.misses = 0 if healthy else self.misses + 1
        # Local reachability cannot prove the market server can reach the card.
        # A global empty registry is a market outage, not a reason for a join storm.
        if hasattr(self.market,'c') and now-self.last_registry_probe>=30:
            self.last_registry_probe=now
            try:
                response=self.market.c.get(self.market.base+'/api/shops',timeout=8)
                if response.status_code==200:
                    shops=response.json().get('shops',[])
                    present=any(s.get('name')==self.args.name and s.get('url','').rstrip('/')==self.proxy.public
                                for s in shops)
                    self.market_visibility='present' if present else 'market_empty' if not shops else 'absent'
                    self.registry_misses=self.registry_misses+1 if shops and not present else 0
                    if self.registry_misses>=2:self.pending_registration=True
            except Exception:
                self.market_visibility='unverified'
        expired_soon = self.provider == 'pinggy' and now - self.started >= 3300
        alternative=self.registration_failures>=2 and self.market_visibility!='market_empty'
        if (expired_soon and not self.pending_registration) or self.misses >= 2 or alternative:
            # Keep the old process in the kit's cleanup list until registration succeeds.
            # A provisioning failure leaves the current tunnel untouched.
            replacement, url = (kit.open_tunnel(self.pport,avoid=self.provider) if alternative else
                                kit.open_tunnel(self.pport))
            # A freshly allocated Cloudflare hostname took ~60 seconds to
            # resolve in production. Match initial startup's readiness budget;
            # discarding it at 30 seconds creates endless hostname churn.
            if not kit.wait_card(url, 120):
                kit.stop(replacement)
                raise RuntimeError('Replacement tunnel failed discovery')
            previous = self.procs[1]
            if self.retired is None:
                self.retired = previous
                self.procs.append(previous)
            else:
                kit.stop(previous)
            self.procs[1] = replacement
            self.provider=getattr(replacement,'r3_provider',self.provider)
            self.proxy.public = self.state['own_shop'] = url
            self.pending_registration = True
            self.registration_failures=0
            self.started, self.misses = now, 0
            kit.log('[tunnel] replacement ready; shop registration pending at ' + url)
        if self.pending_registration and self.market_visibility!='market_empty':
            # A one-shot join must be retried even when the new URL is already healthy.
            try:
                result = self.market.join('shop', self.args.name, self.keys['shop'], self.proxy.public, tries=1)
            except (Exception, SystemExit) as exc:
                self.registration_failures+=1
                kit.log('[market] shop registration pending; retrying in 15 s (' + type(exc).__name__ + ')')
                self.snapshot('registration_retry')
                return
            self.pending_registration = False
            self.registration_failures=0
            self.registry_misses=0
            self.market_visibility='present'
            kit.log(f"[market] shop registered again as {result['name']} at {self.proxy.public}")
            if self.retired is not None:
                kit.stop(self.retired)
                for i in range(len(self.procs) - 1, 1, -1):
                    if self.procs[i] is self.retired:
                        self.procs.pop(i)
                self.retired = None
        self.snapshot('market_discovery_outage' if self.market_visibility=='market_empty' else
                      'market_registration_missing' if self.market_visibility=='absent' else
                      'healthy' if healthy or not self.pending_registration and self.misses == 0 else 'probing')


def supervise(kit, *args):
    monitor = ShopSupervisor(kit, *args)
    while True:
        time.sleep(15)
        try:
            monitor.tick()
        except (Exception, SystemExit) as exc:
            kit.log('[kit] shop supervisor: ' + type(exc).__name__)
            monitor.snapshot('retrying')
