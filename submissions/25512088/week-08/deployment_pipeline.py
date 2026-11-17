"""Fail-closed role deployment state machine.

This module validates deployment evidence.  It never edits source files and it
does not infer success merely because a command or process was started.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import time

from adaptive_control import Control


FORBIDDEN_PARTS={'.git','.market','logs','owner.env'}
ALLOWED_SUFFIXES={'.py','.sh','.json','.yaml','.yml','.md','.ps1'}


class WorktreeGuard:
    def __init__(self,root):
        self.root=Path(root).resolve()

    def check(self,candidate_paths,dirty_paths=()):
        def relative_name(value):
            name=Path(value).as_posix()
            return name[2:] if name.startswith('./') else name
        candidate={relative_name(p) for p in candidate_paths}
        dirty={relative_name(p) for p in dirty_paths}
        reasons=[]
        for relative in candidate:
            path=(self.root/relative)
            try:resolved=path.resolve(strict=False)
            except OSError:reasons.append('unresolvable');continue
            if resolved!=self.root and self.root not in resolved.parents:reasons.append('path_escape')
            parts=set(Path(relative).parts)
            if parts&FORBIDDEN_PARTS or Path(relative).name.endswith(('.pem','.env')):reasons.append('forbidden')
            if path.suffix.lower() not in ALLOWED_SUFFIXES:reasons.append('not_allowlisted')
        if candidate&dirty:reasons.append('dirty_overlap')
        return not reasons,sorted(set(reasons))


class DeploymentPipeline:
    def __init__(self,root,state=None,runner=None):
        self.root=Path(root).resolve();self.control=Control(state or self.root/'.market')
        self.runner=runner or self._run

    @staticmethod
    def _run(argv,cwd,timeout):
        started=time.time()
        try:
            result=subprocess.run(argv,cwd=cwd,stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                timeout=timeout,start_new_session=True)
            return result.returncode,time.time()-started,False,result.stdout[-2000:]
        except subprocess.TimeoutExpired as exc:
            return 124,time.time()-started,True,str(exc)[:500]

    def create(self,commit,role,parameters=None,rollback_target=None):
        return self.control.create_deployment(commit,role,parameters,rollback_target)

    def validate(self,deployment_id,candidate_paths,dirty_paths=(),checks=()):
        ok,reasons=WorktreeGuard(self.root).check(candidate_paths,dirty_paths)
        now=time.time();self.control.validation(deployment_id,'worktree_guard',now,now,0 if ok else 1,
            evidence={'reasons':reasons,'paths':sorted(candidate_paths)})
        if not ok:
            self.control.move_deployment(deployment_id,'QUARANTINED','worktree_guard_failed',{'reasons':reasons})
            return False
        for name,argv in checks:
            started=time.time();code,elapsed,timed_out,output=self.runner(argv,self.root,300)
            self.control.validation(deployment_id,name,started,started+elapsed,code,timed_out,
                {'output_hash':hashlib.sha256(output.encode()).hexdigest()})
            if code or timed_out:
                self.control.move_deployment(deployment_id,'QUARANTINED','validation_failed',{'check':name})
                return False
        self.control.move_deployment(deployment_id,'VALIDATED','all_checks_passed')
        return True

    def shadow(self,deployment_id,post_count,elapsed_seconds,input_count,role):
        required=50 if role=='buyer' else 30
        passed=post_count==0 and (elapsed_seconds>=300 or input_count>=required)
        self.control.move_deployment(deployment_id,'SHADOW' if passed else 'QUARANTINED',
            'shadow_passed' if passed else 'shadow_failed',{'post_count':post_count,'inputs':input_count})
        return passed

    def canary(self,deployment_id,role,samples,elapsed_seconds,supply_windows=0,violations=()):
        enough=(samples>=20 and elapsed_seconds>=600) if role=='buyer' else (samples>=10 and supply_windows>=1)
        if not enough or violations:
            self.control.move_deployment(deployment_id,'QUARANTINED','canary_not_started',{
                'samples':samples,'violations':list(violations)})
            return False
        self.control.move_deployment(deployment_id,'CANARY','canary_evidence_ready',{'samples':samples})
        return True

    def activate(self,deployment_id,*,supervisor_owned,board_alive,commit_matches,checkpoint_valid,alive_seconds):
        truth=supervisor_owned and board_alive and commit_matches and checkpoint_valid and alive_seconds>=90
        if truth:
            self.control.move_deployment(deployment_id,'ACTIVE','runtime_truth_verified')
            self.control.event('deployment','deployment_succeeded',{'deployment_id':deployment_id})
            return True
        self.control.move_deployment(deployment_id,'ROLLED_BACK','runtime_truth_failed',{
            'supervisor_owned':supervisor_owned,'board_alive':board_alive,
            'commit_matches':commit_matches,'checkpoint_valid':checkpoint_valid})
        return False

    def rollback(self,deployment_id,reason='safety_violation'):
        state=self.control.deployment_state(deployment_id)
        target='ROLLED_BACK' if state in {'CANARY','ACTIVE'} else 'QUARANTINED'
        self.control.move_deployment(deployment_id,target,reason)

    def close(self):self.control.close()
