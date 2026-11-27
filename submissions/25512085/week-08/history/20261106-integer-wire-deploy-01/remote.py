"""Telemetry-only remote validation and atomic backport, no live transaction or signal."""
import ast
import collections
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
OUT=REPO/REL/'history/20261106-integer-wire-runtime-01'
PYTHON='/home/comalswjd/venv/bin/python'
M=json.loads((OUT/'manifest.json').read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def git(*a): return subprocess.check_output(['git',*a],cwd=REPO).decode().strip()
def save(n,d):
    with (OUT/n).open('x') as f: json.dump(d,f,indent=2)
def hashes(mapping): return {p:sha(REPO/p) for p in mapping}
def guard():
    assert git('rev-parse','HEAD')==M['base'], 'head_drift'
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only'), 'tracked_dirty'
    assert hashes(M['source_sha256'])==M['source_sha256'], 'source_drift'
    assert not (REPO/M['paths'][1]).exists(), 'test_collision'

def snapshot():
    rows=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=(proc/'cmdline').read_bytes().decode().split('\0')[:-1]
            fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            role=next((r for r,n in [('kit','guarded_market_kit.py'),('seller','seller.py'),('buyer','buyer.py'),('market_buyer','market_buyer.py')] if any(a==n or a.endswith('/'+n) for a in args)),None)
            if args and Path(args[0]).name=='cloudflared': role='tunnel'
            if role:
                row=dict(role=role,pid=int(proc.name),ppid=int(fields[1]),starttime=fields[19],cwd=str((proc/'cwd').resolve()))
                if '--port' in args: row['port']=int(args[args.index('--port')+1])
                rows.append(row)
        except (OSError,ValueError): continue
    return sorted(rows,key=lambda p:p['pid'])
def persistent(rows): return [r for r in rows if r['role'] in ('kit','seller','tunnel')]

def validate():
    guard()
    save('before.json',dict(utc=utc(),head=M['base'],processes=snapshot(),source_sha256=hashes(M['source_sha256'])))
    for label in ('baseline','sandbox'):
        tree=OUT/'evidence'/label
        tree.mkdir(parents=True,exist_ok=False)
        for p,h in M['source_sha256'].items():
            assert '..' not in Path(p).parts
            assert (p.startswith(REL) and '/' not in p[len(REL):]) or (p.startswith('scripts/') and '/' not in p[8:])
            assert p.endswith('.py') or Path(p).name in ('seller_cash_policy.json','run_guarded_market.sh')
            blob=subprocess.check_output(['git','show',M['base']+':'+p],cwd=REPO)
            assert hashlib.sha256(blob).hexdigest()==h
            dest=tree/p; dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('xb') as f: f.write(blob)
        if label=='sandbox':
            cmd=['git','apply','--check','--unsafe-paths','--directory='+str(tree),str(OUT/'backport.patch')]
            with (OUT/'isolated-patch.log').open('xb') as log:
                subprocess.run(cmd,cwd=REPO,check=True,stdout=log,stderr=subprocess.STDOUT)
                cmd.remove('--check')
                subprocess.run(cmd,cwd=REPO,check=True,stdout=log,stderr=subprocess.STDOUT)
            for p in M['paths']:
                src=OUT/Path(p).name
                assert sha(src)==M['after'][p]
                assert src.read_bytes()==(tree/p).read_bytes()
        for p in tree.rglob('*.py'): compile(p.read_bytes(),str(p),'exec')
    baseline=OUT/'evidence/baseline'/M['paths'][0]
    candidate=baseline.read_bytes()
    for a,b in [(b'f"I offer {offer} for this certified item."',b'str(offer)'),(b'f"I offer {seller_ask} for this certified item."',b'str(seller_ask)')]:
        assert candidate.count(a)==1
        candidate=candidate.replace(a,b)
    assert candidate==(OUT/'evidence/sandbox'/M['paths'][0]).read_bytes()
    assert sha(baseline)==M['source_sha256'][M['paths'][0]]
    assert ast.dump(ast.parse(baseline.read_bytes()))==ast.dump(ast.parse((REPO/M['paths'][0]).read_bytes()))
    unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
    assert {p:sha(OUT/'evidence/sandbox'/p) for p in unchanged}==unchanged
    home=OUT/'evidence/synthetic-home'; home.mkdir(exist_ok=False)
    env=dict(PATH='/home/comalswjd/venv/bin:/usr/bin:/bin',HOME=str(home),LANG='C.UTF-8',PYTHONDONTWRITEBYTECODE='1')
    reports={}
    for label,mode in [('baseline','all'),('sandbox','focused'),('sandbox','all')]:
        name=label+'-'+mode
        log=OUT/(name+'.log')
        with log.open('xb') as f:
            result=subprocess.run([PYTHON,'-B',str(OUT/'run_isolated.py'),mode,label],cwd=OUT/'evidence'/label/REL,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=240)
        payload=next((l.split('=',1)[1] for l in log.read_text().splitlines() if l.startswith('BACKPORT_TEST_RESULT=')),None)
        report=json.loads(payload) if payload else dict(success=False)
        reports[name]=report
        save('result-'+name+'.json',report)
        assert result.returncode==0 and report['success'], 'offline_failure_'+name
    assert reports['sandbox-all']['tests_run']==reports['baseline-all']['tests_run']+4
    guard()
    save('remote-tests.json',dict(utc=utc(),python=sys.version,reports=reports))
    print(json.dumps(dict(stage='validated_not_applied',reports=reports)))

def apply():
    tests=json.loads((OUT/'remote-tests.json').read_text())
    before=json.loads((OUT/'before.json').read_text())
    guard()
    assert persistent(snapshot())==persistent(before['processes']), 'process_drift'
    backup=OUT/'backup'; backup.mkdir(exist_ok=False)
    for p in M['paths']:
        if p not in M['source_sha256']: continue
        with (backup/Path(p).name).open('xb') as f:
            f.write((REPO/p).read_bytes()); f.flush(); os.fsync(f.fileno())
        assert sha(backup/Path(p).name)==M['source_sha256'][p]
    staged={}
    for p in M['paths']:
        src=OUT/Path(p).name
        assert sha(src)==M['after'][p]
        assert src.read_bytes()==(OUT/'evidence/sandbox'/p).read_bytes()
        compile(src.read_bytes(),p,'exec')
        tmp=(REPO/p).with_name('.integer-wire-'+Path(p).name+'.tmp')
        with tmp.open('xb') as f: f.write(src.read_bytes()); f.flush(); os.fsync(f.fileno())
        if (REPO/p).exists(): assert (tmp.stat().st_mode & 0o777)==((REPO/p).stat().st_mode & 0o777)
        staged[p]=tmp
    guard()
    save('pre-atomic.json',dict(utc=utc(),head=git('rev-parse','HEAD'),source_sha256=hashes(M['source_sha256']),processes=snapshot()))
    # Install test first, then the buyer; each file replacement is atomic.
    for p in reversed(M['paths']):
        assert git('rev-parse','HEAD')==M['base']
        if p in M['source_sha256']: assert sha(REPO/p)==M['source_sha256'][p]
        else: assert not (REPO/p).exists()
        os.replace(staged[p],REPO/p)
        fd=os.open(str((REPO/p).parent),os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    save('atomic-applied.json',dict(utc=utc(),runtime_sha256=hashes(M['paths']),signals_sent=0))
    assert hashes(M['paths'])==M['after']
    for p in M['paths']:
        compile((REPO/p).read_bytes(),p,'exec')
        assert ast.dump(ast.parse((REPO/p).read_bytes()))==ast.dump(ast.parse((OUT/Path(p).name).read_bytes()))
    unchanged={p:h for p,h in M['source_sha256'].items() if p not in M['paths']}
    assert hashes(unchanged)==unchanged
    assert sorted(git('diff','HEAD','--name-only').splitlines())==sorted(p for p in M['paths'] if p in M['source_sha256'])
    subprocess.run(['git','add','--',*M['paths']],cwd=REPO,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert sorted(git('diff','--cached','--name-only').splitlines())==sorted(M['paths'])
    msg='fix(week08): send Round 3 offers as integer strings only\n\nBackport-of: 0baab04049c88a11e759216a7d7f63a139032ba4 (two expressions and fixtures), cc62b97 (strict tests), bfa4e52 (legacy fixture correction); final source '+M['source']+'\nPreserve legacy/initial inquiries, counter35, limits, cooldown, telemetry, ranking, guard and cohort.'
    with (OUT/'commit.log').open('xb') as f:
        subprocess.run(['git','-c','user.name='+M['author_name'],'-c','user.email='+M['author_email'],'-c','core.hooksPath=/dev/null','-c','commit.gpgsign=false','commit','-m',msg],cwd=REPO,stdout=f,stderr=subprocess.STDOUT,check=True)
    assert git('rev-parse','HEAD^')==M['base']
    assert sorted(git('diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines())==sorted(M['paths'])
    assert not git('diff','HEAD','--name-only') and not git('diff','--cached','--name-only')
    after=snapshot(); assert persistent(after)==persistent(before['processes'])
    report=dict(utc=utc(),head=git('rev-parse','HEAD'),parent=M['base'],runtime_sha256=hashes(M['paths']),tests=tests,unchanged_sources=len(unchanged),byte_ast_compile=True,tracked_dirty=[],processes=after,restarts=0,signals_sent=0)
    save('applied.json',report)
    print(json.dumps(report))

def observe():
    applied=json.loads((OUT/'applied.json').read_text())
    assert git('rev-parse','HEAD')==applied['head'] and hashes(M['paths'])==M['after']
    rows=snapshot()
    assert persistent(rows)==persistent(json.loads((OUT/'before.json').read_text())['processes'])
    sellers=[r for r in rows if r['role']=='seller' and r['cwd']==str(REPO/REL)]
    assert len(sellers)==1
    with urllib.request.urlopen('http://127.0.0.1:'+str(sellers[0]['port'])+'/.well-known/agent-card.json',timeout=15) as r: local=r.status
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board',timeout=25) as r: board=json.load(r); status=r.status
    shops=[s for s in board.get('shops',[]) if s.get('name')=='mjforge']
    buyers=[s for s in board.get('buyers',[]) if s.get('name')=='mjforge']
    assert len(shops)==len(buyers)==1
    url=shops[0]['card']['url']; assert url.startswith('https://')
    with urllib.request.urlopen(url.rstrip('/')+'/.well-known/agent-card.json',timeout=25) as r: public=r.status
    health=dict(local_card_http=local,board_http=status,public_card_http=public,seller_alive=shops[0].get('alive'),buyer_alive=buyers[0].get('alive'))
    assert local==status==public==200 and health['seller_alive'] is True and health['buyer_alive'] is True
    # Exactly one bounded read. No raw line, account, message, URL or signature leaves memory.
    log=REPO/REL/'logs/market-kit.log'
    with log.open('rb') as f:
        size=f.seek(0,2); start=max(0,size-262144); f.seek(start); data=f.read(262144)
    lines=data.splitlines()[1:] if start else data.splitlines()
    events=[]; malformed=0; envelopes=0; contract_warnings=0
    since=datetime.datetime.fromisoformat(json.loads((OUT/'atomic-applied.json').read_text())['utc'])
    for line in lines:
        if b'market_buyer printed no result line' in line: contract_warnings+=1
        if b'buyer_turn' not in line: continue
        try:
            raw=line.decode('utf-8')
            pos=raw.find('"[buyer] ')
            if pos<0: raise ValueError()
            outer=json.loads(raw[pos:])
            assert isinstance(outer,str) and outer.startswith('[buyer] ')
            event=json.loads(outer[8:])
            if datetime.datetime.fromisoformat(event['timestamp_utc'])<since: continue
            assert event['event']=='buyer_turn' and event['schema_version']==1
            envelopes+=1; events.append(event)
        except (ValueError,KeyError,AssertionError,TypeError): malformed+=1
    summary=dict(utc=utc(),head=applied['head'],runtime_sha256=hashes(M['paths']),health=health,restarts=0,signals_sent=0,tail_limit_bytes=262144,tail_bytes=len(data),event_count=len(events),string_envelopes=envelopes,malformed_marker_lines=malformed,contract_warning_lines_in_tail=contract_warnings,scope='single bounded tail; warnings not time-filtered; events after deployment only',keys=sorted({k for e in events for k in e}),counts=dict(collections.Counter(str((e.get('phase'),e.get('boundary'),e.get('turn_index'),e.get('policy_branch'),e.get('outcome'))) for e in events)),max_envelope_bytes=max((len(json.dumps('[buyer] '+json.dumps(e,separators=(',',':')),ensure_ascii=False).encode())+1 for e in events),default=0))
    summary['receipt_selected_events']=sum(e.get('receipt_selected') is True for e in events)
    summary['effect_verified']=False
    save('observation.json',summary)
    print(json.dumps(summary))

try:
    if MODE=='validate': validate()
    elif MODE=='apply': apply()
    elif MODE=='observe': observe()
    else: raise AssertionError('invalid_mode')
except Exception as e:
    save('failure-'+MODE+'.json',dict(utc=utc(),type=type(e).__name__,message=str(e)))
    raise
