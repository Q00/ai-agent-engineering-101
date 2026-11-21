"""Install a hash-pinned official tunnel binary inside the ignored runtime folder."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen
from r3.state import ROOT

URL = 'https://github.com/cloudflare/cloudflared/releases/download/2026.10.7/cloudflared-windows-amd64.exe'
SHA256 = '2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343'


def main():
    target = ROOT / '.market' / 'bin' / 'cloudflared.exe'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != SHA256:
        temporary = target.with_suffix('.download')
        with urlopen(Request(URL, headers={'User-Agent': 'Round3-runtime-preparation'}), timeout=60) as r:
            with temporary.open('wb') as f:
                while True:
                    block = r.read(1024 * 1024)
                    if not block:
                        break
                    f.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('Official binary digest mismatch; binary was not executed')
        os.replace(temporary, target)
    result = subprocess.run([str(target), '--version'], capture_output=True, text=True, check=True)
    record = {'source': URL, 'sha256': SHA256, 'version': result.stdout.strip(),
              'path': str(target), 'tunnel_started': False}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with (ROOT / 'logs' / ('tunnel-preparation-' + stamp + '.json')).open('x', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
