"""Single read-only health observation; no active-task or new-code claims."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
OUT = REPO/REL/'history/20261107-goods-terms-runtime-01'
M = json.loads((OUT/'manifest.json').read_text())
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO).decode().strip()
assert head == M['base']
hashes = {p:hashlib.sha256((REPO/p).read_bytes()).hexdigest() for p in M['source_sha256']}
assert hashes == M['source_sha256']
dirty = subprocess.check_output(['git', 'diff', 'HEAD', '--name-only'], cwd=REPO).decode().splitlines()
assert not dirty
rows = []
for proc in Path('/proc').glob('[0-9]*'):
    try:
        args = (proc/'cmdline').read_bytes().decode().split('\0')[:-1]
        fields = (proc/'stat').read_text().rsplit(')', 1)[1].split()
        role = next((r for r,n in [('kit','guarded_market_kit.py'), ('seller','seller.py')] if any(a == n or a.endswith('/'+n) for a in args)), None)
        if args and Path(args[0]).name == 'cloudflared':
            role = 'tunnel'
        if role:
            row = dict(role=role, pid=int(proc.name), ppid=int(fields[1]), starttime=fields[19], cwd=str((proc/'cwd').resolve()))
            if '--port' in args:
                row['port'] = int(args[args.index('--port')+1])
            rows.append(row)
    except (OSError, ValueError):
        continue
sellers = [r for r in rows if r['role'] == 'seller' and r['cwd'] == str(REPO/REL)]
assert len(sellers) == 1
with urllib.request.urlopen('http://127.0.0.1:'+str(sellers[0]['port'])+'/.well-known/agent-card.json', timeout=15) as r:
    local_status = r.status
    card = json.load(r)
with urllib.request.urlopen('https://market.ourolabs.ai/api/board', timeout=25) as r:
    board_status = r.status
    board = json.load(r)
shops = [s for s in board.get('shops', []) if s.get('name') == 'mjforge']
buyers = [s for s in board.get('buyers', []) if s.get('name') == 'mjforge']
assert len(shops) == len(buyers) == 1
url = shops[0]['card']['url']
assert url.startswith('https://')
with urllib.request.urlopen(url.rstrip('/')+'/.well-known/agent-card.json', timeout=25) as r:
    public_status = r.status
health = dict(local_card_http=local_status, board_http=board_status, public_card_http=public_status, seller_alive=shops[0].get('alive'), buyer_alive=buyers[0].get('alive'), card_version=card.get('version'))
# One bounded read; no content or individual dialogue/receipt/account is exported.
with (REPO/REL/'logs/market-kit.log').open('rb') as f:
    size = f.seek(0, 2)
    start = max(0, size-65536)
    f.seek(start)
    data = f.read(65536)
lines = data.splitlines()[1:] if start else data.splitlines()
markers = {name:sum(token in line for line in lines) for name,token in [
    ('seller_marker_lines', b'[seller]'), ('seller_quote_marker_lines', b'[seller] [quote]'),
    ('seller_stock_miss_lines', b'[seller] [stock-miss]'), ('seller_restart_lines', b'your seller exited')]}
report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status='HOLD_NOT_DEPLOYED', head=head, source_sha256=hashes, tracked_dirty=dirty, processes=rows, health=health, restarts=0, signals_sent=0, production_replacements=0, tail_limit_bytes=65536, tail_bytes=len(data), bounded_markers=markers, pending_sessions='unknown; memory-only state, no drain/pending endpoint', new_behavior_observed=False, effect_verified=False)
with (OUT/'hold-observation.json').open('x') as f:
    json.dump(report, f, indent=2)
print(json.dumps(report))
