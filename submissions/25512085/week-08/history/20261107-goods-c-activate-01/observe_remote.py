"""Single bounded post-activation GET health and aggregate-only log observation."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

REPO=Path('/home/comalswjd/ai-agent-engineering-101')
REL='submissions/25512085/week-08/'
WORK=REPO/REL
OUT=WORK/'history/20261107-goods-c-activate-runtime-01'
success=json.loads((OUT/'success.json').read_text())
manifest=json.loads((WORK/'history/20261107-goods-c-only-runtime-06/manifest.json').read_text())
def git(*args):
    return subprocess.check_output(['git',*args],cwd=REPO).decode().strip()
def proc(pid):
    p=Path('/proc')/str(pid)
    f=(p/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=pid,ppid=int(f[1]),starttime=f[19],cwd=str((p/'cwd').resolve()))
hashes={p:hashlib.sha256((REPO/p).read_bytes()).hexdigest() for p in manifest['source_sha256']}
expected=dict(manifest['source_sha256']); expected.update(manifest['after'])
assert all(hashlib.sha256((REPO/p).read_bytes()).hexdigest()==h for p,h in expected.items())
assert git('rev-parse','HEAD')==success['remote_commit']
assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
processes={role:proc(success[role]['pid']) for role in ('seller','kit','tunnel')}
for role,p in processes.items(): assert p['starttime']==success[role]['starttime']
assert proc(processes['seller']['ppid'])['ppid']==43679
seller_pids=[]
for p in Path('/proc').glob('[0-9]*'):
    try:
        args=(p/'cmdline').read_bytes().split(b'\0')[:-1]
        if len(args)==4 and args[1:]==[b'seller.py',b'--port',b'52507'] and (p/'cwd').resolve()==WORK:
            seller_pids.append(int(p.name))
    except (FileNotFoundError,ProcessLookupError,PermissionError): pass
assert seller_pids==[success['seller']['pid']], 'seller_duplicate_or_drift'
health={}
def get(url,label):
    try:
        with urllib.request.urlopen(url,timeout=20) as r:
            data=json.load(r); health[label]=r.status
            return data
    except Exception as exc:
        health[label]=type(exc).__name__
        return None
card=get('http://127.0.0.1:52507/.well-known/agent-card.json','local_card_http')
board=get('https://market.ourolabs.ai/api/board','board_http')
if isinstance(board,dict):
    shops=[s for s in board.get('shops',[]) if s.get('name')=='mjforge']
    buyers=[s for s in board.get('buyers',[]) if s.get('name')=='mjforge']
    health.update(own_shop_rows=len(shops),own_buyer_rows=len(buyers))
    if len(shops)==1:
        health['seller_alive']=shops[0].get('alive')
        url=shops[0].get('card',{}).get('url','')
        parsed=urllib.parse.urlsplit(url)
        assert parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query
        get(url.rstrip('/')+'/.well-known/agent-card.json','public_card_http')
    if len(buyers)==1: health['buyer_alive']=buyers[0].get('alive')
# Read one bounded tail in the VM; export counts only, never raw text or original log.
path=WORK/'logs/market-kit.log'
with path.open('rb') as f:
    size=f.seek(0,2); start=max(0,size-65536); f.seek(start); raw=f.read(65536)
lines=raw.splitlines()[1:] if start else raw.splitlines()
needle=b'Started server process ['+str(success['seller']['pid']).encode()+b']'
marks=[i for i,line in enumerate(lines) if needle in line]
after=lines[marks[-1]+1:] if marks else []
pilot=(b'G021aae64',b'G0be9ed07',b'G81ade83e')
log=dict(tail_limit_bytes=65536,tail_bytes=len(raw),new_pid_marker_found=bool(marks),
         post_start_lines=len(after),seller_quote_lines=sum(b'[seller]' in l and b'[r3-quote]' in l for l in after),
         pilot_C0_quote_lines=sum(b'[seller]' in l and b'[r3-quote]' in l and b'grade=C,' in l
                                  and b'warranty=0h' in l and any(s in l for s in pilot) for l in after),
         post_start_http_200_lines=sum(b'[seller]' in l and b'200 OK' in l for l in after))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=git('rev-parse','HEAD'),
            processes=processes,source_sha256=hashes,fixture_sha256=expected[REL+'test_goods_terms_pilot_offline.py'],
            unrelated_source_files_unchanged=55,health=health,bounded_log_observation=log,
            pending_sessions_lost='unknown',new_C0_effect_verified=False,
            observation_note='Aggregates cannot establish unrestricted-request eligibility or transaction effects.',
            requests_sent='GET only; no transaction/RPC/POST generated')
with (OUT/'observation.json').open('x') as f: json.dump(report,f,indent=2)
print(json.dumps(report))
assert all(health.get(k)==200 for k in ('local_card_http','board_http','public_card_http')), 'health_not_all_200'
