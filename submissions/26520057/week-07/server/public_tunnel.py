"""Maintain a public lab-shop URL; re-register the buyer kit after tunnel changes."""
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

root = Path(__file__).resolve().parent.parent
state = root / '.market'
command = ['/usr/bin/ssh','-T','-o','StrictHostKeyChecking=accept-new','-o','ServerAliveInterval=30',
           '-o','ServerAliveCountMax=3','-o','ExitOnForwardFailure=yes',
           '-R','80:127.0.0.1:8900','nokey@localhost.run']

def atomic(path, text):
    temp = path.with_suffix('.next')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd,'w') as f:
        f.write(text)
    temp.replace(path)

with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) as process:
    try:
        for line in process.stdout:
            match = re.search(r'https://[a-z0-9-]+\.lhr\.life\b', line)
            if not match or 'tunneled' not in line:
                continue
            url = match.group(0)
            env_path = state / 'gpt.env'
            lines = [l for l in env_path.read_text().splitlines() if not l.startswith('SHOP_PUBLIC_URL=')]
            atomic(env_path, '\n'.join(lines)+f'\nSHOP_PUBLIC_URL={url}\n')
            subprocess.run(['systemctl','--user','restart','market-gpt-shop'],check=True)
            for attempt in range(12):
                try:
                    with urllib.request.urlopen(url+'/.well-known/agent-card.json',timeout=5) as response:
                        if response.status == 200:
                            break
                except Exception:
                    time.sleep(1)
            else:
                raise RuntimeError('Public tunnel health check failed')
            atomic(state/'gpt-public-url', url+'\n')
            print('[public-url]',url,flush=True)
            if (state/'gpt-cutover-enabled').exists():
                subprocess.run(['systemctl','--user','restart','market-buyer'],check=True)
                print('[public-url] market registration restarted',flush=True)
        raise SystemExit(process.wait() or 1)
    finally:
        process.terminate()
