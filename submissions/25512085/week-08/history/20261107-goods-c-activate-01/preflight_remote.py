"""Approved seller-only activation; exact two-file update and watchdog restart."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import urllib.request

REPO = Path('/home/comalswjd/ai-agent-engineering-101')
REL = 'submissions/25512085/week-08/'
WORK = REPO / REL
STAGE = WORK / 'history/20261107-goods-c-only-runtime-06'
OUT = WORK / 'history/20261107-goods-c-activate-runtime-01'
manifest = json.loads((STAGE/'manifest.json').read_text())
assert manifest['base'] == '4a5deb685368343abfd1310c572288a924351b01'
assert manifest['source'] == '2fbd842b2bf5b02716e479fc81099ee4bbe1e7a0'
assert manifest['after'] == {
    REL+'seller.py':'17c653f836436975268651436e9f7cf0e1bfda9b1fdfeb6615de4b67df4a13fd',
    REL+'test_goods_terms_pilot_offline.py':'ad9568062c09b6689925078b284b1a5ab356620ab5dc10fc40ef2ceb5a4432cf'}
def git(*args):
    return subprocess.check_output(['git',*args],cwd=REPO,stderr=subprocess.PIPE,timeout=30).decode().strip()
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def proc(pid):
    p = Path('/proc')/str(pid)
    f = (p/'stat').read_text().rsplit(')',1)[1].split()
    return dict(pid=pid, ppid=int(f[1]), starttime=f[19], state=f[0], cwd=str((p/'cwd').resolve()))
def identity(pid, start):
    assert proc(pid)['starttime'] == start, 'process_identity_drift'
def protected():
    identity(43679,'1037901')
    identity(43696,'1038186')
def check_sources(after=False):
    for p,h in manifest['source_sha256'].items():
        assert sha(REPO/p) == (manifest['after'][p] if after and p in manifest['after'] else h), 'source_drift:'+p
def atomic(path, data, label, mode=0o644):
    temp = path.with_name('.'+path.name+'.goods-c-activate-01-'+label)
    with temp.open('xb') as f:
        os.fchmod(f.fileno(),mode)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)
def save(name, data):
    with (OUT/name).open('xb') as f:
        f.write(json.dumps(data,indent=2).encode()); f.flush(); os.fsync(f.fileno())
def candidates():
    result=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            args=(p/'cmdline').read_bytes().split(b'\0')[:-1]
            if len(args)==4 and args[1:]==[b'seller.py',b'--port',b'52507'] and (p/'cwd').resolve()==WORK:
                result.append(proc(int(p.name)))
        except (FileNotFoundError,PermissionError,ProcessLookupError): pass
    return result
def healthy():
    try:
        with urllib.request.urlopen('http://127.0.0.1:52507/.well-known/agent-card.json',timeout=2) as r:
            data=json.load(r)
            return r.status==200 and isinstance(data,dict) and bool(data.get('name'))
    except Exception: return False
def await_seller(excluded, seconds=105):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        protected()
        active=[p for p in candidates() if p['pid'] not in excluded]
        assert len(active)<=1, 'duplicate_seller'
        if active and healthy(): return active[0]
        time.sleep(2)
    raise RuntimeError('seller_restart_health_deadline')
def commit(message):
    git('add','--',*manifest['paths'])
    git('-c','user.name=MJforge','-c','user.email=25512085@users.noreply.github.com',
        'commit','--only','-m',message,'--',*manifest['paths'])
    return git('rev-parse','HEAD')

assert git('rev-parse','HEAD')==manifest['base']
assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
check_sources(); protected(); identity(299169,'7735542'); identity(299168,'7735541')
assert proc(299169)['ppid']==299168 and proc(299168)['ppid']==43679
assert (Path('/proc/299168/cmdline').read_bytes().split(b'\0')[:-1]
        == [b'/bin/sh',b'-c',b'python seller.py --port 52507']), 'seller_parent_command_drift'
assert not (REPO/manifest['paths'][1]).exists()
assert git('apply','--check',str(STAGE/'backport.patch')) == ''
blobs={p:(STAGE/'evidence/sandbox'/p).read_bytes() for p in manifest['paths']}
for p,b in blobs.items():
    assert hashlib.sha256(b).hexdigest()==manifest['after'][p]
    compile(b,p,'exec')
assert healthy(), 'baseline_card_unhealthy'

print(json.dumps(dict(preflight=True, card_status=200, seller_parent_command_verified=True, patch_check=True, source_files=len(manifest["source_sha256"]))))
