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
OUT.mkdir(exist_ok=False)
original=(WORK/'seller.py').read_bytes()
mode=(WORK/'seller.py').stat().st_mode & 0o777
with (OUT/'seller.py.before').open('xb') as f:
    f.write(original); f.flush(); os.fsync(f.fileno())
report=dict(base=manifest['base'],source=manifest['source'],pending_sessions='unknown_risk_accepted',
            started_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),signals=[],manual_spawns=0)
logpath=(Path('/proc/43679/fd/1')).resolve()
if logpath.is_relative_to(WORK/'logs') and logpath.is_file():
    report['log_observation']={'path':str(logpath),'offset':logpath.stat().st_size}
save('before.json',report)
changed=[]
try:
    # Recheck immediately before writes; no full sync or old helper execution.
    assert git('rev-parse','HEAD')==manifest['base']
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
    check_sources(); protected(); identity(299169,'7735542')
    for p,b in blobs.items():
        if p in manifest['source_sha256']: assert sha(REPO/p)==manifest['source_sha256'][p]
        else: assert not (REPO/p).exists()
        atomic(REPO/p,b,'apply',mode if p.endswith('/seller.py') else 0o644)
        changed.append(p)
    check_sources(True)
    assert set(git('diff','HEAD','--name-only').splitlines())=={REL+'seller.py'}
    report['remote_commit']=commit('Activate approved revision06 A0 exclusion and C-first seller')
    assert git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()==sorted(manifest['paths'])
    save('applied.json',report)
    protected(); identity(299169,'7735542')
    os.kill(299169,signal.SIGTERM)
    report['signals'].append({'pid':299169,'starttime':'7735542','signal':'TERM'})
    save('signalled.json',report)
    # Existing kit watchdog owns restarting. Never spawn a second seller.
    new=await_seller({299169})
    report['seller']=new
    report['activated_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    report['activated_kst']=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat()
    check_sources(True); protected()
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
    report.update(success=True,card_status=200,seller_sha256=sha(WORK/'seller.py'),unrelated_source_files_unchanged=55,
                  kit=proc(43679),tunnel=proc(43696))
    save('success.json',report)
except Exception as exc:
    report.update(success=False,failure_type=type(exc).__name__)
    save('failed.json',report)
    # Restore only bytes this action wrote; never overwrite concurrent user edits.
    for p in changed: assert sha(REPO/p)==manifest['after'][p], 'rollback_concurrent_edit_stop'
    if REL+'seller.py' in changed: atomic(WORK/'seller.py',original,'rollback',mode)
    fixture=REPO/manifest['paths'][1]
    if manifest['paths'][1] in changed: fixture.unlink()
    if 'remote_commit' in report:
        report['rollback_commit']=commit('Rollback failed revision06 seller activation; preserve failed evidence')
    else:
        # Restore only the two staging entries if commit failed.
        git('restore','--staged','--',*manifest['paths'])
    active=candidates()
    for p in active:
        if p['pid']!=299169:
            identity(p['pid'],p['starttime']); os.kill(p['pid'],signal.SIGTERM)
            report['signals'].append({'pid':p['pid'],'starttime':p['starttime'],'signal':'TERM_rollback'})
    report['restored_seller']=(proc(299169) if not report['signals'] and healthy() else await_seller({p['pid'] for p in active}))
    check_sources(); protected()
    save('rollback.json',report)
print(json.dumps(report))
assert report['success'], 'activation_failed_rollback_recorded'
