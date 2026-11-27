"""Explicit-path remote validation/apply only; never starts a live buyer or sends signals."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

REPO=Path('/home/comalswjd/ai-agent-engineering-101')
REL='submissions/25512085/week-08/'
OUT=REPO/REL/'history/20261106-forum-cohort-runtime-01'
PYTHON='/home/comalswjd/venv/bin/python'
M=json.loads((OUT/'manifest.json').read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def git(*args): return subprocess.check_output(['git',*args],cwd=REPO).decode().strip()
def save(name,data):
    with (OUT/name).open('x') as f: json.dump(data,f,indent=2)
def hashes(mapping): return {p:sha(REPO/p) for p in mapping}
def guard():
    assert git('rev-parse','HEAD')==M['base'], 'head_drift'
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only'), 'tracked_dirty'
    assert hashes(M['source_sha256'])==M['source_sha256'], 'source_drift'
    assert not (REPO/M['paths'][3]).exists(), 'new_test_collision'

def snapshot():
    rows=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=(proc/'cmdline').read_bytes().decode().split('\0')[:-1]
            fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            role=next((r for r,n in [('kit','guarded_market_kit.py'),('seller','seller.py'),('buyer','buyer.py'),('market_buyer','market_buyer.py'),('restock','restock.py')] if any(a==n or a.endswith('/'+n) for a in args)),None)
            if args and Path(args[0]).name=='cloudflared': role='tunnel'
            if role:
                row=dict(role=role,pid=int(proc.name),ppid=int(fields[1]),starttime=fields[19],cwd=str((proc/'cwd').resolve()),exe=str((proc/'exe').resolve()))
                if '--port' in args: row['port']=int(args[args.index('--port')+1])
                rows.append(row)
        except (OSError,ValueError): continue
    return sorted(rows,key=lambda p:p['pid'])
def persistent(rows): return [r for r in rows if r['role'] in ('kit','seller','tunnel')]
def public_check(port):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/.well-known/agent-card.json',timeout=10) as r: local=r.status
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board',timeout=25) as r: board=json.load(r); status=r.status
    shops=[s for s in board.get('shops',[]) if s.get('name')=='mjforge']
    buyers=[s for s in board.get('buyers',[]) if s.get('name')=='mjforge']
    assert len(shops)==len(buyers)==1
    url=shops[0]['card']['url']; assert url.startswith('https://')
    with urllib.request.urlopen(url.rstrip('/')+'/.well-known/agent-card.json',timeout=25) as r: public=r.status
    result=dict(utc=utc(),local_card_http=local,board_http=status,public_card_http=public,registered_url=url,seller_alive=shops[0].get('alive'),buyer_alive=buyers[0].get('alive'))
    assert local==status==public==200 and result['seller_alive'] is True and result['buyer_alive'] is True
    return result

def validate():
    guard()
    protected=REPO/REL/'history/20261106-procurement-guard-runtime-041439-085d36'
    prior={str(protected/n):sha(protected/n) for n in ('execution-final.json','remote-tests.json')}
    save('before.json',dict(utc=utc(),head=M['base'],processes=snapshot(),source_sha256=hashes(M['source_sha256']),prior_evidence=prior))
    tree=OUT/'evidence/sandbox'; tree.mkdir(parents=True,exist_ok=False)
    for p,h in M['source_sha256'].items():
        assert (p.startswith(REL) or p.startswith('scripts/')) and '..' not in Path(p).parts
        blob=subprocess.check_output(['git','show',M['base']+':'+p],cwd=REPO)
        assert hashlib.sha256(blob).hexdigest()==h
        dest=tree/p; dest.parent.mkdir(parents=True,exist_ok=True)
        with dest.open('xb') as f: f.write(blob)
    assert sha(OUT/'backport.patch')==M['patch_sha256']
    cmd=['git','apply','--check','--unsafe-paths','--directory='+str(tree),str(OUT/'backport.patch')]
    with (OUT/'isolated-patch.log').open('x') as log:
        subprocess.run(cmd,cwd=REPO,stdout=log,stderr=subprocess.STDOUT,check=True)
        cmd.remove('--check'); subprocess.run(cmd,cwd=REPO,stdout=log,stderr=subprocess.STDOUT,check=True)
    assert {p:sha(tree/p) for p in M['paths']}==M['after']
    unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
    assert {p:sha(tree/p) for p in unchanged}==unchanged
    for p in tree.rglob('*.py'): compile(p.read_bytes(),str(p),'exec')
    home=OUT/'evidence/synthetic-home'; home.mkdir(exist_ok=False)
    env=dict(PATH='/home/comalswjd/venv/bin:/usr/bin:/bin',HOME=str(home),LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1')
    reports={}
    for mode,count in [('focused',193),('all',426)]:
        log=OUT/('remote-'+mode+'.log')
        with log.open('xb') as f:
            result=subprocess.run([PYTHON,'-B',str(OUT/'run_isolated.py'),mode,'sandbox'],env=env,cwd=tree/REL,stdout=f,stderr=subprocess.STDOUT,timeout=240)
        payload=next((l.split('=',1)[1] for l in log.read_text().splitlines() if l.startswith('BACKPORT_TEST_RESULT=')),None)
        reports[mode]=json.loads(payload) if payload else dict(success=False)
        save('result-'+mode+'.json',reports[mode])
        assert result.returncode==0 and reports[mode]['success'] and reports[mode]['tests_run']==count, 'isolated_tests_failed_'+mode
    guard()
    save('remote-tests.json',dict(utc=utc(),python=sys.version,pre_runtime_apply=True,reports=reports))
    print(json.dumps(dict(stage='validated_not_applied',reports=reports)))

def apply():
    tests=json.loads((OUT/'remote-tests.json').read_text()); assert tests['pre_runtime_apply']
    before=json.loads((OUT/'before.json').read_text())
    guard()
    assert persistent(snapshot())==persistent(before['processes']), 'persistent_process_drift'
    assert {p:sha(p) for p in before['prior_evidence']}==before['prior_evidence']
    backup=OUT/'backup'; backup.mkdir(exist_ok=False)
    tree=OUT/'evidence/sandbox'
    staged={}
    for p in M['paths']:
        original=REPO/p
        assert sha(tree/p)==M['after'][p]
        compile((tree/p).read_bytes(),p,'exec')
        if M['before'][p] is not None:
            with (backup/Path(p).name).open('xb') as f: f.write(original.read_bytes()); f.flush(); os.fsync(f.fileno())
            assert sha(backup/Path(p).name)==M['before'][p]
        tmp=original.with_name('.forum-cohort-'+original.name+'.tmp')
        with tmp.open('xb') as f:
            f.write((tree/p).read_bytes()); f.flush(); os.fsync(f.fileno())
        if original.exists(): assert (tmp.stat().st_mode & 0o777)==(original.stat().st_mode & 0o777)
        staged[p]=tmp
    guard()
    save('pre-atomic.json',dict(utc=utc(),head=git('rev-parse','HEAD'),dirty=[],source_sha256=hashes(M['source_sha256']),processes=snapshot()))
    applied=[]
    # Observer first: strict writer must never precede proof collection.
    for p in M['paths']:
        assert git('rev-parse','HEAD')==M['base']
        if M['before'][p] is not None: assert sha(REPO/p)==M['before'][p]
        else: assert not (REPO/p).exists()
        os.replace(staged[p],REPO/p)
        fd=os.open(str((REPO/p).parent),os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
        applied.append(dict(path=p,utc=utc(),sha256=sha(REPO/p)))
    save('atomic-applied.json',dict(utc=utc(),files=applied,signals_sent=0))
    assert hashes(M['paths'])==M['after']
    for p in M['paths']: compile((REPO/p).read_bytes(),p,'exec')
    unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
    assert hashes(unchanged)==unchanged
    assert sorted(git('diff','HEAD','--name-only').splitlines())==sorted(p for p in M['paths'] if M['before'][p] is not None)
    subprocess.run(['git','add','--',*M['paths']],cwd=REPO,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert sorted(git('diff','--cached','--name-only').splitlines())==sorted(M['paths'])
    msg='fix(week08): backport mature forum cohort proof only\n\nBackport-of: '+M['source']+'\nPreserve monolithic cash pricing, procurement guard, buyer gap and ranking.'
    with (OUT/'commit.log').open('xb') as f:
        subprocess.run(['git','-c','user.name='+M['author_name'],'-c','user.email='+M['author_email'],'-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','commit','-m',msg],cwd=REPO,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert git('rev-parse','HEAD^')==M['base']
    assert sorted(git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())==sorted(M['paths'])
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
    after=snapshot(); assert persistent(after)==persistent(before['processes'])
    report=dict(utc=utc(),head=git('rev-parse','HEAD'),parent=M['base'],source=M['source'],runtime_sha256=hashes(M['paths']),tests=tests,processes=after,signals_sent=0,restarts=0,tracked_dirty=[],prior_evidence_unchanged={p:sha(p) for p in before['prior_evidence']}==before['prior_evidence'])
    save('applied.json',report)
    sellers=[r for r in after if r['role']=='seller' and r['cwd']==str(REPO/REL)]; assert len(sellers)==1
    report['public']=public_check(sellers[0]['port'])
    assert hashes(M['paths'])==M['after']
    save('final.json',report)
    print(json.dumps(report))

try:
    if MODE=='validate': validate()
    elif MODE=='apply': apply()
    elif MODE=='export':
        names=['before.json','isolated-patch.log','remote-focused.log','remote-all.log','result-focused.json','result-all.json','remote-tests.json','pre-atomic.json','atomic-applied.json','commit.log','finish-commit.log','failure-apply.json','applied.json','final.json','backup/seller_cash_policy.py','backup/forum_writer.py','backup/test_forum_intel_offline.py']
        import base64
        print(json.dumps({n:base64.b64encode((OUT/n).read_bytes()).decode() for n in names if (OUT/n).exists()}))
    else: raise AssertionError('invalid_mode')
except Exception as e:
    save('failure-'+MODE+'.json',dict(utc=utc(),type=type(e).__name__,message=str(e)))
    raise
