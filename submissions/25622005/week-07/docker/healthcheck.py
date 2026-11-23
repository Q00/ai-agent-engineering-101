"""Local liveness only; public reachability and registration need separate checks."""
import json
import os
from pathlib import Path
import urllib.request


def main():
    for name in ('runtime', 'kit'):
        pid = int(Path(f'/data/.market/{name}.pid').read_text().strip())
        os.kill(pid, 0)
    with urllib.request.urlopen('http://127.0.0.1:8900/.well-known/agent-card.json', timeout=5) as response:
        card = json.load(response)
    if 'psyoung82' not in card.get('name', '') or not card.get('supportedInterfaces'):
        raise ValueError('Unexpected local seller card')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit(f'Local market service is not ready: {error}')
