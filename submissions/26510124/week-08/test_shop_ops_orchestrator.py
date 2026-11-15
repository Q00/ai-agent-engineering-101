"""Offline operations lifecycle fixtures. No CLI models, real market or service calls."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shop_operations as ops
import shop_ops_commands as commands
from shop_ops_policy import (ROOT, PolicyStore, atomic_write, baseline_state, config_at,
                             current_state, digest, read_json, read_overlay, state_dir)
from shop_policy import policy_at


class OperationsTests(unittest.TestCase):
    NOW=1794700000
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cfg=json.loads((ROOT/'shop-ops-config.json').read_text())
        atomic_write(self.root/'shop-ops-config.json',self.cfg)
        atomic_write(self.root/'shop-policy.json',policy_at())
        atomic_write(self.root/'advisor-config.json',{'enabled':False})
        atomic_write(self.root/'shop-ops-rules.json',{'version':'3.6','round':3,'verified_at':self.NOW,
                     'ends_at':'2026-11-17T23:59:00+09:00'})
        self.candidate={'sku':'G12345678','grade':'A','warranty_hours':6,'available_units':2,'eligible':True,
            'reasons':[], 'metrics':{'tasks':30,'confirmed_sales':10,'price_rejected':4,'errors':0,
                'settlement_refused':0,'sufficient_maturity':True,'net_estimate':100,
                'refund_adjusted_profit_per_visit':3.33,'settled_revenue_per_hour':80}}
        self.obs={'schema':1,'generated_at':self.NOW,'round':3,'rules_version':'3.6','market_now':self.NOW-780,
            'application_ready':True,'blockers':[],'candidates':[self.candidate], 'sources':{},
            'current':{'observed_tasks':30,'groups':[dict(self.candidate['metrics'],sku='G12345678',grade='A',warranty_hours=6)]},
            'previous':{},'diagnostics':{},'inventory_analysis':{},'policy_cohorts':{},'limits':[]}
        self.calls=0

    def collector(self,root,*,now,rules):
        result=deepcopy(self.obs);result['generated_at']=now;result['market_now']=now-780
        return result

    def model(self,context,root,cfg):
        self.calls+=1
        return {'action':'experiment','targets':['G12345678:A:6'],'diagnosis':'Four explicit price rejections and profitable mature sales.',
                'expected_effect':'Test a one-percent opening quote reduction.'}

    def tick(self,**kw):
        return ops.tick(self.root,now=kw.pop('now',self.NOW),model=kw.pop('model',self.model),collector=self.collector,**kw)

    def proposal(self,result):
        return read_json(state_dir(self.root)/'decisions'/(result['decision_id']+'.json'))

    def auto(self):
        self.cfg['mode']='auto';atomic_write(self.root/'shop-ops-config.json',self.cfg)

    def test_proposal_mode_does_not_change_active_policy_or_buyer(self):
        atomic_write(self.root/'buyer-policy.json',{'unchanged':True})
        before=(self.root/'buyer-policy.json').read_bytes()
        result=self.tick()
        self.assertEqual(result['status'],'proposed')
        self.assertIsNone(read_overlay(self.root,now=self.NOW,rnd=3))
        self.assertEqual((self.root/'buyer-policy.json').read_bytes(),before)
        self.assertEqual(len(list((state_dir(self.root)/'outbox').glob('*.json'))),1)
        self.assertFalse((self.root/'.market/outbox.jsonl').exists())

    def test_auto_applies_and_cadence_does_not_call_model_or_flood_outbox(self):
        self.auto();result=self.tick()
        self.assertEqual(result['status'],'applied',result)
        state=current_state(self.root)
        self.assertEqual(state['changes'][0]['after'],-1)
        self.assertEqual(self.tick(now=self.NOW+60)['status'],'cadence_wait')
        # One initial evaluation is permitted; repeated minute checks are not.
        before=len(list((state_dir(self.root)/'snapshots').glob('*.json')))
        self.tick(now=self.NOW+120)
        self.assertEqual(len(list((state_dir(self.root)/'snapshots').glob('*.json'))),before)
        self.assertEqual(self.calls,1)

    def test_model_failure_bad_json_and_unverified_targets_hold_baseline(self):
        cases=[RuntimeError('private failure'), '{', {'action':'experiment','targets':['BAD'],
            'diagnosis':'bad','expected_effect':'bad'}]
        for index,bad in enumerate(cases):
            with self.subTest(bad=bad):
                def model(*args):
                    if isinstance(bad,Exception):raise bad
                    return bad
                result=self.tick(now=self.NOW+index*1800,model=model)
                self.assertEqual(result['status'],'hold')
                self.assertIsNone(read_overlay(self.root,now=self.NOW+index*1800))
                self.assertNotIn('private failure',json.dumps(self.proposal(result)))

    def test_stale_evidence_prevents_model_call_and_experiment(self):
        self.obs.update(application_ready=False,blockers=['inventory_stale'])
        self.assertEqual(self.tick()['status'],'hold')
        self.assertEqual(self.calls,0)

    def test_error_cohort_stops_only_current_experiment(self):
        self.auto();p=self.proposal(self.tick());state=current_state(self.root)
        self.obs['policy_cohorts'][state['version']]={'groups':[
            {**self.candidate['metrics'],'sku':'G12345678','grade':'A','warranty_hours':6,'errors':1}]}
        obs=ops.snapshot(self.root,self.NOW+60,self.collector)
        result=ops.evaluate(self.root,p,obs,now=self.NOW+60)
        self.assertEqual(result['status'],'rolled_back')
        self.assertEqual(current_state(self.root)['changes'],[])
        notifications=len(list((state_dir(self.root)/'outbox').glob('*.json')))
        ops.evaluate(self.root,p,obs,now=self.NOW+120)
        self.assertEqual(len(list((state_dir(self.root)/'outbox').glob('*.json'))),notifications)

    def test_checkout_refusal_wakes_but_only_matched_cohort_can_rollback(self):
        self.auto();self.tick();version=current_state(self.root)['version']
        self.tick(now=self.NOW+60)  # Consume the initial routine evaluation.
        checkout=self.root/'.market/checkout-events.jsonl'
        def event(at,stage='settlement_refused'):
            checkout.write_text(json.dumps({'time':at,'task':'task-one','unit':'unit-one','stage':stage})+'\n')
        # A genuinely uncertain payment is not a refusal and must not wake us.
        event(self.NOW+90,'settlement_uncertain')
        count=len(list((state_dir(self.root)/'snapshots').glob('*.json')))
        self.tick(now=self.NOW+120)
        self.assertEqual(len(list((state_dir(self.root)/'snapshots').glob('*.json'))),count)
        event(self.NOW+121)
        self.tick(now=self.NOW+180)
        self.assertGreater(len(list((state_dir(self.root)/'snapshots').glob('*.json'))),count)
        self.assertEqual(current_state(self.root)['version'],version)
        # An already observed refusal must not cause repeated minute evaluations.
        count=len(list((state_dir(self.root)/'snapshots').glob('*.json')))
        self.tick(now=self.NOW+240)
        self.assertEqual(len(list((state_dir(self.root)/'snapshots').glob('*.json'))),count)
        # The snapshot collector is responsible for exact task/unit/cohort joins.
        self.obs['policy_cohorts'][version]={'groups':[
            {**self.candidate['metrics'],'sku':'G12345678','grade':'A','warranty_hours':6,'settlement_refused':1}]}
        event(self.NOW+241)
        self.tick(now=self.NOW+270)
        self.assertEqual(current_state(self.root)['reason'],'technical_error')
        self.assertEqual(self.calls,1)

    def test_new_experiment_reserves_six_hours_for_last_sale_maturity(self):
        rules=read_json(self.root/'shop-ops-rules.json')
        for margin,blocked in ((-1,True),(0,False)):
            with self.subTest(margin=margin):
                rules['ends_at']=datetime.fromtimestamp(
                    self.NOW-780+self.cfg['experiment_seconds']+6*3600+margin,timezone.utc).isoformat()
                atomic_write(self.root/'shop-ops-rules.json',rules)
                obs=ops.snapshot(self.root,self.NOW,self.collector)
                self.assertEqual('insufficient_time_for_experiment' in obs['blockers'],blocked)
                self.assertEqual(obs['application_ready'],not blocked)

    def test_insufficient_observations_never_performance_rollback(self):
        self.auto();p=self.proposal(self.tick());version=current_state(self.root)['version']
        obs=ops.snapshot(self.root,self.NOW+10800,self.collector)
        result=ops.evaluate(self.root,p,obs,now=self.NOW+10800)
        self.assertEqual(result['status'],'insufficient_sample')
        self.assertEqual(current_state(self.root)['version'],version)

    def test_authenticated_apply_resumes_after_commit_before_acknowledgement(self):
        p=self.proposal(self.tick())
        key=self.root/'operator.key';commands.init_key(key)
        envelope=commands.signed_request('apply',key_path=key,request_id='request-12345',now=self.NOW,
            args={'decision_id':p['decision_id'],'expected_version':p['base_version']})
        commands.accept(self.root,envelope,key_path=key,now=self.NOW)
        obs=ops.snapshot(self.root,self.NOW,self.collector)
        # Simulate process death after policy pointer replacement, before request ack.
        applied=PolicyStore(self.root).apply(p,obs,now=self.NOW,authorized=True)
        ops.process_requests(self.root,obs,now=self.NOW+1)
        row=read_json(state_dir(self.root)/'requests/request-12345.json')
        self.assertEqual(row['state'],'verifying')
        self.assertEqual(current_state(self.root)['version'],applied['version'])
        # Matching request ID receives final expiry result, not acknowledgement only.
        ops.tick(self.root,now=self.NOW+21601,no_model=True,collector=self.collector)
        row=read_json(state_dir(self.root)/'requests/request-12345.json')
        self.assertEqual(row['state'],'verifying')
        self.assertEqual(current_state(self.root)['changes'],[])
        fixed={'experiment':{'groups':[]},'baseline':{'groups':[]},'comparable':False}
        with patch('shop_ops_evidence.evaluate_windows',return_value=fixed) as evaluate:
            ops.tick(self.root,now=self.NOW+43201,no_model=True,collector=self.collector)
        self.assertEqual(evaluate.call_args.kwargs['version'],applied['version'])
        self.assertEqual(evaluate.call_args.kwargs['start'],applied['created_at'])
        self.assertEqual(evaluate.call_args.kwargs['end'],applied['expires_at'])
        row=read_json(state_dir(self.root)/'requests/request-12345.json')
        self.assertEqual(row['state'],'completed')
        self.assertEqual(row['decision_id'],p['decision_id'])

    def test_old_evaluation_cannot_restore_new_policy(self):
        self.auto();p=self.proposal(self.tick());old=current_state(self.root)
        PolicyStore(self.root).restore(old['version'],now=self.NOW+1,authorized=True)
        newer=self.proposal(self.tick(now=self.NOW+1800))
        state=current_state(self.root)
        obs=ops.snapshot(self.root,self.NOW+1801,self.collector)
        self.assertEqual(ops.evaluate(self.root,p,obs,now=self.NOW+1801)['status'],'superseded')
        self.assertEqual(current_state(self.root)['version'],state['version'])

    def test_model_settings_and_command_never_read_trading_profile(self):
        import shop_ops_model
        with patch('shop_ops_model.backend.load_profile',side_effect=AssertionError('shared profile read')), \
             patch('shop_ops_model.backend.ensure_chatgpt'),patch('shop_ops_model.backend.execute') as run, \
             patch('shop_ops_model.backend.answer',return_value='{"action":"hold","targets":[],"diagnosis":"safe","expected_effect":"none"}'):
            run.return_value.returncode=0
            answer=shop_ops_model.analyse({'test':True},self.root,self.cfg)
            self.assertEqual(answer['action'],'hold')
            self.assertIn('gpt-6.1-sol',run.call_args.args[0])
            self.assertNotIn('--yolo',run.call_args.args[0])


if __name__=='__main__': unittest.main()
