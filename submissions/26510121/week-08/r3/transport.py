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
from .state import ROOT, atomic_json


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
        self.provider = provider or os.environ.get('MARKET_TUNNEL_PROVIDER', 'pinggy')
        self.started = time.monotonic() if now is None else now
        self.misses = 0
        self.pending_registration = False
        self.retired = None

    def snapshot(self, status):
        atomic_json(self.directory / '.market/shop-health.json', {
            'checked_at': time.time(), 'status': status, 'url': self.proxy.public,
            'pending_registration': self.pending_registration, 'consecutive_probe_failures': self.misses,
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
        expired_soon = self.provider == 'pinggy' and now - self.started >= 3300
        if (expired_soon and not self.pending_registration) or self.misses >= 2:
            # Keep the old process in the kit's cleanup list until registration succeeds.
            # A provisioning failure leaves the current tunnel untouched.
            replacement, url = kit.open_tunnel(self.pport)
            if not kit.wait_card(url, 30):
                kit.stop(replacement)
                raise RuntimeError('Replacement tunnel failed discovery')
            previous = self.procs[1]
            if self.retired is None:
                self.retired = previous
                self.procs.append(previous)
            else:
                kit.stop(previous)
            self.procs[1] = replacement
            self.proxy.public = self.state['own_shop'] = url
            self.pending_registration = True
            self.started, self.misses = now, 0
            kit.log('[tunnel] replacement ready; shop registration pending at ' + url)
        if self.pending_registration:
            # A one-shot join must be retried even when the new URL is already healthy.
            try:
                result = self.market.join('shop', self.args.name, self.keys['shop'], self.proxy.public, tries=1)
            except (Exception, SystemExit) as exc:
                kit.log('[market] shop registration pending; retrying in 15 s (' + type(exc).__name__ + ')')
                self.snapshot('registration_retry')
                return
            self.pending_registration = False
            kit.log(f"[market] shop registered again as {result['name']} at {self.proxy.public}")
            if self.retired is not None:
                kit.stop(self.retired)
                for i in range(len(self.procs) - 1, 1, -1):
                    if self.procs[i] is self.retired:
                        self.procs.pop(i)
                self.retired = None
        self.snapshot('healthy' if healthy or not self.pending_registration and self.misses == 0 else 'probing')


def supervise(kit, *args):
    monitor = ShopSupervisor(kit, *args)
    while True:
        time.sleep(15)
        try:
            monitor.tick()
        except (Exception, SystemExit) as exc:
            kit.log('[kit] shop supervisor: ' + type(exc).__name__)
            monitor.snapshot('retrying')
