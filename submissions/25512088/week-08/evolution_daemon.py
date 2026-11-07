"""Closed-loop evidence -> Ouroboros candidate -> validation -> canary deploy.

Only bounded structured local evidence enters the generated Seed.  Forum text,
credentials and raw logs never enter the model context.  A candidate is built
in a detached worktree and cannot touch live code before every local check
passes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import uuid

from adaptive_control import Control
from deployment_pipeline import WorktreeGuard


HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
PREFIX=Path('submissions/25512088/week-08')
UV=Path('/home/ohs/.local/share/pipx/venvs/uv/bin/uv')
OUROBOROS_VERSION='ouroboros-ai[mcp]==0.59.9'
INTERVAL=1800
BUILD_TIMEOUT=1800
CANARY_TIMEOUT=7200
ROLE_FILES={
    'buyer':{'buyer_r3.py','buyer_publicist.py','rival_feedback.py','shop_copy.py'},
    'shop':{'referral_seller.py','seller_strategy.py','stock_pricing.py','strategic_market.py',
            'procurement_campaign.py','referral_campaign.py','growth_offers.py'},
}
ALWAYS_ALLOWED={'adaptive_control.py','adaptive_stats.py','safety_kernel.py'}


def run(argv,cwd,timeout,input_bytes=None):
    process=subprocess.Popen(argv,cwd=cwd,stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    try:out,_=process.communicate(input_bytes,timeout=timeout);return process.returncode,out[-20000:]
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGTERM)
        try:out,_=process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);out,_=process.communicate()
        return 124,out[-20000:]


class Evolution:
    def __init__(self,state='.market',clock=time.time,runner=run):
        self.state=Path(state).resolve();self.dir=self.state/'evolution';self.dir.mkdir(parents=True,exist_ok=True)
        self.control=Control(self.state);self.clock=clock;self.runner=runner
        self.control.db.executescript('''
        CREATE TABLE IF NOT EXISTS evolution_cycles(
          cycle_id TEXT PRIMARY KEY, created REAL, updated REAL, trigger TEXT,
          role TEXT, state TEXT, base_commit TEXT, candidate_commit TEXT,
          evidence_hash TEXT, deployment_id TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS evolution_baselines(
          baseline_id TEXT PRIMARY KEY, observed REAL, rank INTEGER, score REAL,
          top10_gap REAL, board_hash TEXT);
        ''')
        self.ensure_baseline()

    def emit(self,kind,body=None,correlation=None):
        self.control.event('evolution',kind,body or {},correlation)

    def evidence(self,now=None):
        now=self.clock() if now is None else now
        ranks=[dict(r) for r in self.control.db.execute(
            'SELECT observed,our_score,our_rank,top_score,cutoffs FROM rank_observations WHERE observed>? ORDER BY observed',
            (now-7200,))]
        events=dict(self.control.db.execute(
            "SELECT kind,COUNT(*) FROM runtime_events WHERE ts>? AND role IN ('buyer','seller','shop','procurement') GROUP BY kind",
            (now-1800,)).fetchall())
        inputs={r['source']:{'hash':r['content_hash'],'fetched':r['fetched'],'schema_ok':bool(r['schema_ok'])}
            for r in self.control.db.execute("SELECT source,fetched,content_hash,schema_ok FROM input_snapshots WHERE source IN ('rules','agent','board','shops')")}
        latest=ranks[-1] if ranks else None;old=None
        if latest:
            old=next((row for row in ranks if latest['observed']-row['observed']>=1440),None)
        gap=None;old_gap=None
        if latest:
            cuts=json.loads(latest['cutoffs']);gap=max(0,float(cuts.get('10',latest['top_score']))-latest['our_score'])
        if old:
            cuts=json.loads(old['cutoffs']);old_gap=max(0,float(cuts.get('10',old['top_score']))-old['our_score'])
        return {'observed_at':now,'rank':latest['our_rank'] if latest else None,
            'score':latest['our_score'] if latest else None,'top10_gap':gap,
            'top10_gap_24m_ago':old_gap,'event_counts_30m':events,'official_inputs':inputs}

    def trigger(self,evidence,now=None):
        now=self.clock() if now is None else now
        last=self.control.db.execute('SELECT created,evidence_hash,state,base_commit FROM evolution_cycles ORDER BY created DESC LIMIT 1').fetchone()
        rules=(evidence['official_inputs'].get('rules') or {}).get('hash')
        previous_rules=self.control.db.execute(
            "SELECT body FROM runtime_events WHERE role='evolution' AND kind='cycle_started' ORDER BY id DESC LIMIT 1").fetchone()
        if previous_rules:
            try:changed=json.loads(previous_rules[0]).get('rules_hash')!=rules
            except (ValueError,TypeError):changed=False
            if changed:return 'official_rule_changed'
        if last and last['state'] in {'QUARANTINED','ROLLBACK_FAILED'}:
            current=self.runner(['git','rev-parse','HEAD'],REPO,30)[1].decode().strip()
            if current and current!=last['base_commit']:return 'implementation_updated'
        if evidence['event_counts_30m'].get('role_exited',0):return 'runtime_failure'
        if not last:return 'scheduled_review'
        if last and now-last['created']<INTERVAL:return None
        if evidence['top10_gap'] is not None and evidence['top10_gap_24m_ago'] is not None:
            if evidence['top10_gap']>=evidence['top10_gap_24m_ago']-1e-12:return 'rank_gap_not_closing'
        return None

    def ensure_baseline(self):
        if self.control.db.execute('SELECT 1 FROM evolution_baselines LIMIT 1').fetchone():return
        try:evidence=self.evidence()
        except sqlite3.OperationalError:return
        board=evidence['official_inputs'].get('board') or {}
        with self.control.db:self.control.db.execute('INSERT INTO evolution_baselines VALUES(?,?,?,?,?,?)',(
            'startup',evidence['observed_at'],evidence['rank'],evidence['score'],evidence['top10_gap'],board.get('hash')))

    def write_performance_report(self,evidence):
        baseline=self.control.db.execute('SELECT * FROM evolution_baselines ORDER BY observed LIMIT 1').fetchone()
        if not baseline:return
        improved=(evidence['score'] is not None and baseline['score'] is not None and evidence['score']>baseline['score']) or (
            evidence['rank'] is not None and baseline['rank'] is not None and evidence['rank']<baseline['rank'])
        report={'baseline':dict(baseline),'current':{
            'observed':evidence['observed_at'],'rank':evidence['rank'],'score':evidence['score'],
            'top10_gap':evidence['top10_gap']},'improved':bool(improved)}
        path=self.dir/'morning-report.json';tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(report,sort_keys=True,indent=2));tmp.replace(path)

    def choose_role(self,evidence):
        # Lower official component is the bottleneck; fall back to fewer useful events.
        board=self.control.db.execute("SELECT body FROM input_snapshots WHERE source='board'").fetchone()
        if board:
            try:
                players=json.loads(board[0]).get('players',[])
                ours=next(p for p in players if (p.get('owner') or {}).get('login')=='morethanwhatiwant')
                return 'shop' if (ours.get('shop') or {}).get('all',0)<(ours.get('buyer') or {}).get('all',0) else 'buyer'
            except (ValueError,TypeError,KeyError,StopIteration):pass
        return 'shop' if evidence['event_counts_30m'].get('quote_created',0)<evidence['event_counts_30m'].get('order_result',0) else 'buyer'

    def _cycle(self,cycle_id,trigger,role,evidence,base):
        digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.control.db:self.control.db.execute('INSERT INTO evolution_cycles VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (cycle_id,self.clock(),self.clock(),trigger,role,'CREATED',base,None,digest,None,json.dumps(evidence,sort_keys=True)))
        self.control.transition('evolution',cycle_id,None,'CREATED',trigger,{'cycle_id':cycle_id,'role':role})
        self.emit('cycle_started',{'cycle_id':cycle_id,'role':role,'trigger':trigger,
            'rules_hash':(evidence['official_inputs'].get('rules') or {}).get('hash')},cycle_id)

    def _state(self,cycle_id,new,reason,body=None):
        old=self.control.db.execute('SELECT state FROM evolution_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()[0]
        with self.control.db:self.control.db.execute('UPDATE evolution_cycles SET state=?,updated=?,body=? WHERE cycle_id=?',
            (new,self.clock(),json.dumps(body or {},sort_keys=True),cycle_id))
        self.control.transition('evolution',cycle_id,old,new,reason,body or {},cycle_id)

    def seed(self,cycle_id,role,evidence,project_dir):
        role_tests=(['test_buyer_r3.py','test_buyer_adaptive.py'] if role=='buyer' else
            ['test_referral_seller.py','test_procurement.py','test_rollout.py'])
        tests=' && '.join(f'python {PREFIX}/{name}' for name in role_tests)
        full_tests=f"python -m unittest discover -s {PREFIX} -p 'test_*.py' -q"
        allowed=sorted(ROLE_FILES[role]|ALWAYS_ALLOWED|{f'test_{role}.py'})
        constraints=[
            'Modify only submissions/25512088/week-08 and only the target role.',
            'Never read owner.env, pem files, secrets, or raw forum/log text.',
            'Never execute market mutations; implementation and tests only.',
            'Preserve idempotency, cash floor, inventory limit 250, no self-trade and fail-closed recovery.',
            'Allowed strategy files: '+', '.join(allowed),
        ]
        constraint_yaml='\n'.join('- '+json.dumps(value,ensure_ascii=False) for value in constraints)
        evidence_json=json.dumps(evidence,sort_keys=True,separators=(',',':'))
        behavior_key='ac_'+hashlib.sha256(f'{role}:behavior'.encode()).hexdigest()[:16]
        regression_key='ac_'+hashlib.sha256(f'{role}:regression'.encode()).hexdigest()[:16]
        body=f'''goal: Improve the Round 3 {role} using bounded structured evidence while preserving every safety invariant.
task_type: code
project_dir: {project_dir}
constraints:
{constraint_yaml}
evidence: {json.dumps(evidence_json)}
acceptance_criteria:
- description: Target-role behavior is evidence-driven, bounded, logged, and backward compatible.
  semantic_ac_key: {behavior_key}
  verify_command: {json.dumps(tests)}
  expected_artifacts:
  - submissions/25512088/week-08
- description: The complete local regression suite passes without weakening safety checks.
  semantic_ac_key: {regression_key}
  verify_command: {json.dumps(full_tests)}
  expected_artifacts:
  - submissions/25512088/week-08
ontology_schema:
  name: Round3{role.title()}Evolution
  description: Bounded evidence, candidate code, validation, canary and rollback for one market role.
  fields:
  - name: evidence_snapshot
    type: object
    description: Official hashes, rank gap and bounded event counts used for the decision.
    required: true
  - name: candidate_change
    type: object
    description: Target role paths, validation result, commit and rollback evidence.
    required: true
evaluation_principles:
- name: safety
  description: Existing safety and idempotency invariants have priority over score improvement.
  weight: 0.6
- name: measured_improvement
  description: Prefer changes supported by official score and runtime evidence.
  weight: 0.4
metadata:
  seed_id: {cycle_id}
  version: 1.0.0
  created_at: '2026-11-08T00:00:00Z'
  ambiguity_score: 0.0
  gate_forced: false
  parent_seed_id: null
  generation_mode: normal
  degraded: false
  unresolved_slots: []
  recovery_reason: null
'''
        path=self.dir/f'{cycle_id}.seed.yaml';path.write_text(body);return path

    def build(self,cycle_id,role,evidence,base):
        worktree=Path(tempfile.mkdtemp(prefix=f'market-evolve-{cycle_id[:8]}-'))
        shutil.rmtree(worktree)
        code,out=self.runner(['git','worktree','add','--detach',str(worktree),base],REPO,60)
        if code:return None,[worktree],'worktree_add'
        before=self.registered_worktrees()
        seed=self.seed(cycle_id,role,evidence,worktree)
        command=[str(UV),'tool','uvx','--isolated','--python','>=3.12','--from',OUROBOROS_VERSION,
            'ouroboros','run','workflow',str(seed),'--project-dir',str(worktree),'--runtime','codex',
            '--sequential','--auto-evaluate','--auto-evolve']
        code,out=self.runner(command,worktree,BUILD_TIMEOUT)
        (HERE/'logs'/f'evolution-{cycle_id}.log').write_bytes(out)
        after=self.registered_worktrees();nested=sorted(after-before,key=lambda p:len(str(p)),reverse=True)
        generated=nested[0] if nested else worktree
        cleanup=nested+[worktree]
        if code:return None,cleanup,'ouroboros_failed'
        return generated/PREFIX,cleanup,None

    def registered_worktrees(self):
        code,out=self.runner(['git','worktree','list','--porcelain'],REPO,60)
        if code:return set()
        return {Path(line.decode().split(' ',1)[1]) for line in out.splitlines() if line.startswith(b'worktree ')}

    def candidate_patch(self,candidate_root,worktree,role,base):
        self.runner(['git','add','-A','--',str(PREFIX)],worktree,60)
        code,names=self.runner(['git','diff','--cached','--name-only',base,'--',str(PREFIX)],worktree,60)
        paths=[line.decode().strip() for line in names.splitlines() if line.strip()]
        local=[str(Path(p).relative_to(PREFIX)) for p in paths]
        ok,reasons=WorktreeGuard(HERE).check(local,())
        allowed=ROLE_FILES[role]|ALWAYS_ALLOWED
        for path in local:
            name=Path(path).name
            if not (name in allowed or name.startswith('test_') or path.startswith('tests/')):reasons.append('cross_role')
        code,patch=self.runner(['git','diff','--cached','--binary',base,'--',str(PREFIX)],worktree,60)
        lower=patch.lower()
        if any(marker in lower for marker in (b'private key',b'market_owner=',b'owner.env')):reasons.append('secret_pattern')
        return (ok and not reasons and bool(paths)),sorted(set(reasons)),paths,patch

    def validate_candidate(self,candidate_root):
        python=REPO/'.venv/bin/python'
        for argv in ([str(python),'-m','compileall','-q','.'],[str(python),'-m','unittest','discover','-q']):
            code,_=self.runner(argv,candidate_root,300)
            if code:return False
        return True

    def restart(self,role,cycle_id,timeout=120):
        request=self.state/'supervisor/requests'/f'{cycle_id}.json';result=self.state/'supervisor/results'/f'{cycle_id}.json'
        request.parent.mkdir(parents=True,exist_ok=True);result.parent.mkdir(parents=True,exist_ok=True)
        tmp=request.with_suffix('.tmp');tmp.write_text(json.dumps({'action':'restart','role':role,'cycle_id':cycle_id}));tmp.replace(request)
        deadline=self.clock()+timeout
        while self.clock()<deadline:
            if result.exists():
                body=json.loads(result.read_text());return bool(body.get('healthy'))
            time.sleep(1)
        return False

    def deploy(self,cycle_id,role,base,patch):
        dirty=self.runner(['git','status','--porcelain','--untracked-files=no'],REPO,60)[1]
        if dirty.strip():return False,None,'dirty_worktree'
        code,_=self.runner(['git','apply','--index','-'],REPO,60,patch)
        if code:return False,None,'apply_failed'
        code,_=self.runner(['git','commit','-m',f'evolve {role} {cycle_id}'],REPO,120)
        if code:
            self.runner(['git','apply','-R','--index','-'],REPO,60,patch)
            return False,None,'commit_failed'
        commit=self.runner(['git','rev-parse','HEAD'],REPO,30)[1].decode().strip()
        if not self.restart(role,cycle_id):
            self.runner(['git','revert','--no-edit',commit],REPO,120);self.restart(role,cycle_id+'-rollback')
            return False,commit,'restart_failed'
        return True,commit,None

    def recover_interrupted(self):
        rows=self.control.db.execute("SELECT cycle_id,state,role,candidate_commit FROM evolution_cycles WHERE state IN ('CREATED','VALIDATED','CANARY') ORDER BY created").fetchall()
        for row in rows:
            if row['state']!='CANARY':
                self._state(row['cycle_id'],'QUARANTINED','interrupted_before_live_completion');continue
            commit=row['candidate_commit']
            code,_=self.runner(['git','revert','--no-edit',commit],REPO,120) if commit else (1,b'')
            restarted=code==0 and self.restart(row['role'],row['cycle_id']+'-recovery-rollback')
            self._state(row['cycle_id'],'ROLLED_BACK' if restarted else 'ROLLBACK_FAILED',
                'interrupted_canary_recovered' if restarted else 'interrupted_canary_recovery_failed')

    def canary(self,cycle_id,role,commit,started=None):
        started=self.clock() if started is None else started
        minimum=20 if role=='buyer' else 10;kind='order_result' if role=='buyer' else 'quote_created'
        deadline=started+CANARY_TIMEOUT
        while self.clock()<deadline:
            samples=self.control.db.execute('SELECT COUNT(*) FROM runtime_events WHERE role=? AND kind=? AND ts>=?',
                ('buyer' if role=='buyer' else 'seller',kind,started)).fetchone()[0]
            uncertain=self.control.db.execute("SELECT COUNT(*) FROM mutations WHERE state='UNCERTAIN' AND updated>=?",(started,)).fetchone()[0]
            exits=self.control.db.execute("SELECT COUNT(*) FROM runtime_events WHERE kind='role_exited' AND ts>=?",(started,)).fetchone()[0]
            elapsed=self.clock()-started
            supply_window=True
            if role=='shop':
                try:
                    strategy=json.loads((self.state/'strategy.json').read_text())
                    supply_window=float(strategy.get('selected_at',0))>=started
                except (OSError,ValueError,TypeError):supply_window=False
            if uncertain or exits:return False,{'samples':samples,'uncertain':uncertain,'exits':exits}
            if samples>=minimum and elapsed>=600 and supply_window:
                return True,{'samples':samples,'elapsed':elapsed,'supply_window':supply_window}
            time.sleep(30)
        return False,{'reason':'canary_timeout'}

    def cycle(self,trigger=None):
        evidence=self.evidence();self.ensure_baseline();self.write_performance_report(evidence);trigger=trigger or self.trigger(evidence)
        if not trigger:return None
        role=self.choose_role(evidence);cycle_id='ev-'+uuid.uuid4().hex[:16]
        base=self.runner(['git','rev-parse','HEAD'],REPO,30)[1].decode().strip();self._cycle(cycle_id,trigger,role,evidence,base)
        candidate,worktrees,error=self.build(cycle_id,role,evidence,base)
        try:
            if error:self._state(cycle_id,'QUARANTINED',error);return cycle_id
            ok,reasons,paths,patch=self.candidate_patch(candidate,candidate.parents[2],role,base)
            if not ok or not self.validate_candidate(candidate):
                self._state(cycle_id,'QUARANTINED','candidate_validation_failed',{'reasons':reasons});return cycle_id
            self._state(cycle_id,'VALIDATED','all_candidate_checks_passed',{'paths':paths})
            deployed,commit,error=self.deploy(cycle_id,role,base,patch)
            if not deployed:self._state(cycle_id,'ROLLED_BACK' if commit else 'QUARANTINED',error);return cycle_id
            with self.control.db:self.control.db.execute('UPDATE evolution_cycles SET candidate_commit=? WHERE cycle_id=?',(commit,cycle_id))
            self._state(cycle_id,'CANARY','role_restarted',{'commit':commit})
            passed,body=self.canary(cycle_id,role,commit)
            if passed:self._state(cycle_id,'ACTIVE','canary_passed',body)
            else:
                code,_=self.runner(['git','revert','--no-edit',commit],REPO,120)
                restarted=code==0 and self.restart(role,cycle_id+'-rollback')
                self._state(cycle_id,'ROLLED_BACK' if restarted else 'ROLLBACK_FAILED',
                    'canary_failed' if restarted else 'rollback_failed',body)
            return cycle_id
        finally:
            for worktree in worktrees or ():
                if worktree.exists():self.runner(['git','worktree','remove','--force',str(worktree)],REPO,60)

    def loop(self,once=False):
        lock=(self.dir/'evolution.lock').open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('evolution daemon already owns the lock')
        try:
            self.recover_interrupted()
            while True:
                try:self.cycle()
                except Exception as exc:self.emit('cycle_error',{'error':type(exc).__name__})
                if once:return
                time.sleep(30)
        finally:self.control.close();lock.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--state',default='.market');ap.add_argument('--once',action='store_true')
    args=ap.parse_args();Evolution(args.state).loop(args.once)


if __name__=='__main__':main()
