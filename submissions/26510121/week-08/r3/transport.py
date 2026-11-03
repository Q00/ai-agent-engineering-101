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
from .state import ROOT


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
