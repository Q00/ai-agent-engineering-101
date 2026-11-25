"""No capacity is invented by changing a soft setting under a binding cap."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import meta_effects as effects
import seller_meta as meta
from seller_meta_policy import DEFAULT,POLICY_VERSION
from market_scoring import VERSION
import test_meta_harness as fixtures


def observed(config=None):
    return {'active_policy':config or DEFAULT,
        'risk':{'cash_balance':28_851_151,'stock_acquisition_cost':6_602_876,
                'available_to_invest':1_756_490,'operating_policy':{'phase':'growth',
                    'allow_replenishment':True,'allow_new_skus':True}},
        'inventory_state':{'stock_units':96,'pending_units':32,'max_units':180,
                           'stocked_variety':56,'minimum_variety':50},'market_opportunities':[]}


class EffectCalculationTests(unittest.TestCase):
    def test_target_guidance_scales_with_capital_after_removing_absolute_cap(self):
        d=observed();effect=effects.evaluate(d,DEFAULT,{**DEFAULT,'target_capital_share':.7})
        self.assertTrue(effect['has_effect'])
        self.assertEqual(effect['before']['target_stock_cost'],int((28_851_151+6_602_876)*.65))
        self.assertEqual(effect['after']['target_stock_cost'],int((28_851_151+6_602_876)*.70))
        self.assertEqual(effect['effective_budget_delta'],0)
        self.assertEqual(effect['effect_types'],['target_guidance_only'])
        eligible,excluded=effects.options(d)
        self.assertTrue(any(r['parameter']=='target_capital_share' for r in eligible))

    def test_budget_edit_below_unchanged_cash_exposure_ceiling_is_not_effective(self):
        effect=effects.evaluate(observed(),DEFAULT,{**DEFAULT,'window_budget':6_000_000})
        self.assertFalse(effect['has_effect']);self.assertEqual(effect['effective_budget_delta'],0)

    def test_real_budget_expansion_reports_exact_gain_and_candidate_unlock(self):
        before={**DEFAULT,'window_budget':6_000_000};after={**before,'window_budget':9_000_000}
        d=observed(before);d['risk']['available_to_invest']=9_400_000
        d['market_opportunities']=[{'sku':'bed','cost_ceiling_with_max_surge':8_000_000,
                                   'blocked_by':['current_window_budget']},
                                  {'sku':'food','cost_ceiling_with_max_surge':8_000_000,
                                   'blocked_by':['current_window_budget','food_trial_shelf_policy']}]
        result=effects.evaluate(d,before,after)
        self.assertTrue(result['has_effect']);self.assertEqual(result['effective_budget_delta'],3_000_000)
        self.assertEqual(result['displayed_candidates_unlocked'],['bed'])

    def test_epsilon_edit_is_excluded_while_recovery_forces_exploration(self):
        result=effects.evaluate(observed(),DEFAULT,{**DEFAULT,'epsilon':.35})
        self.assertFalse(result['has_effect'])
        self.assertEqual(result['before']['effective_exploration_probability'],1)
        self.assertIn('exploration_forced_by_recovery',result['no_effect_reasons'])

    def test_epsilon_changes_branch_probability_when_recovery_is_not_binding(self):
        d=observed();d['risk'].update(cash_balance=3_000_000,stock_acquisition_cost=7_000_000,available_to_invest=500_000)
        result=effects.evaluate(d,DEFAULT,{**DEFAULT,'epsilon':.35})
        self.assertTrue(result['has_effect']);self.assertEqual(result['exploration_probability_delta'],.05)
        d['inventory_state']['stocked_variety']=10
        self.assertFalse(effects.evaluate(d,DEFAULT,{**DEFAULT,'epsilon':.35})['has_effect'])

    def test_guidance_change_is_not_reported_as_additional_spend_capacity(self):
        d=observed();result=effects.evaluate(d,DEFAULT,{**DEFAULT,'focus':'discovery'})
        self.assertEqual(result['effect_types'],['focus_guidance_only'])
        self.assertEqual(result['effective_budget_delta'],0)
        d['risk']['operating_policy']['phase']='harvest'
        self.assertFalse(effects.evaluate(d,DEFAULT,{**DEFAULT,'focus':'discovery'})['has_effect'])

    def test_no_unit_room_prevents_budget_edit_from_becoming_an_actionable_gain(self):
        d=observed();d['inventory_state']['stock_units']=180;d['inventory_state']['pending_units']=0
        d['risk']['available_to_invest']=9_400_000
        self.assertFalse(effects.evaluate(d,{**DEFAULT,'window_budget':6_000_000},DEFAULT)['has_effect'])


class MetaEffectGuardTests(unittest.TestCase):
    setUp=fixtures.MetaHarnessTests.setUp
    fresh=fixtures.MetaHarnessTests.fresh
    decision=fixtures.MetaHarnessTests.decision

    def test_model_sees_missing_snapshot_coverage_as_unknown(self):
        self.fresh()
        d=meta.snapshot(self.root,self.now,[])
        self.assertIsNone(d['demand_observation_health']['latest_snapshot_age_seconds'])
        self.assertFalse(d['demand_observation_health']['recent_observation_available'])
        path=self.root/'logs/shop-events.jsonl'
        with path.open('a') as output:
            output.write(json.dumps({'event':'inventory_snapshot','round':3,'source_time':self.now-60,'stock':{}})+'\n')
        d=meta.snapshot(self.root,self.now,[])
        self.assertEqual(d['demand_observation_health']['latest_snapshot_age_seconds'],60)
        self.assertTrue(d['demand_observation_health']['recent_observation_available'])

    def test_meta_sees_lost_window_time_and_actual_binding_spend_limit(self):
        row={'event':'portfolio_execution','at':self.now-1,'decision_id':'approval',
             'approved_units':8,'emitted_units':3,'cut_units':5,'planning_elapsed_seconds':150,
             'lines':[{'sku':'new','kind':'exploration','approved_qty':5,'emitted_qty':0,
                       'cut_qty':5,'reasons':['supply_window_time']}]}
        (self.root/'logs/shop-events.jsonl').write_text(json.dumps(row)+'\n')
        d=meta.snapshot(self.root,self.now,[])
        self.assertEqual(d['procurement_execution']['cut_units_by_reason'],{'supply_window_time':5})
        self.assertEqual(d['procurement_execution']['recent_cases'][0]['decision_id'],'approval')
        self.assertEqual(d['constraint_breakdown']['binding_spend_limits'],['inventory_exposure_after_pending'])

    def test_model_cannot_apply_exposure_increase_when_window_budget_already_binds(self):
        self.inv['balance']=30_000_000;self.fresh()
        def choose(role,prompt,schema):
            self.assertTrue(prompt['snapshot']['ineffective_policy_changes'])
            self.assertFalse(any(r['parameter']=='inventory_capital_share' and r['numeric_value']>.8
                                 for r in prompt['snapshot']['policy_change_options']))
            return {**self.decision(),'parameter':'inventory_capital_share','numeric_value':.9}
        result=meta.review(self.root,now=self.now,public=[],model=choose)
        self.assertEqual(result['status'],'ineffective_change_blocked')
        self.assertEqual(result['decision']['action'],'hold')
        self.assertEqual(result['proposed_decision']['action'],'change')
        self.assertEqual(result['before'],result['after'])
        self.assertEqual(json.loads((self.root/'state/seller-meta-policy.json').read_text())['config'],DEFAULT)

    def test_change_is_rechecked_against_inventory_refreshed_after_model(self):
        clock=[self.now]
        def choose(role,prompt,schema):
            self.assertTrue(any(r['parameter']=='window_budget' for r in prompt['snapshot']['policy_change_options']))
            clock[0]+=5;self.inv['balance']=800_000;self.fresh(clock[0])
            return self.decision()
        with patch.object(meta.time,'time',side_effect=lambda:clock[0]):
            result=meta.review(self.root,public=[],model=choose)
        self.assertEqual(result['status'],'ineffective_change_blocked')
        self.assertEqual(result['change_effect']['before']['effective_window_budget'],200_000)
        self.assertEqual(result['change_effect']['effective_budget_delta'],0)

    def test_effective_expansion_still_obeys_maturity_gate_then_applies(self):
        config={**DEFAULT,'window_budget':6_000_000}
        old={'schema_version':2,'policy_contract_version':POLICY_VERSION,'score_version':VERSION,'version':'previous','config':config,
             'previous_config':DEFAULT,'at':self.now-100,'expires_at':self.now+1000,
             'changed_at':1,'baseline':{'at':1,'book':{'book':0}}}
        (self.root/'state/seller-meta-policy.json').write_text(json.dumps(old))
        r=meta.review(self.root,now=self.now,public=[],model=lambda *a:self.decision(7_500_000))
        self.assertEqual(r['status'],'evaluation_pending')
        self.assertEqual(r['after']['window_budget'],6_000_000)
        rows=[{'event':'settled','at':self.now-1000,'ledger':i,'price':100} for i in range(20)]
        (self.root/'logs/shop-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.fresh(self.now+1801)
        # Keep the tested runtime policy active across the next review.
        old=json.loads((self.root/'state/seller-meta-policy.json').read_text())
        old['expires_at']=self.now+3000;(self.root/'state/seller-meta-policy.json').write_text(json.dumps(old))
        r=meta.review(self.root,now=self.now+1801,public=[],model=lambda *a:self.decision(7_500_000))
        self.assertEqual(r['status'],'changed');self.assertEqual(r['after']['window_budget'],7_500_000)
        self.assertEqual(r['change_effect']['effective_budget_delta'],1_500_000)

    def test_ineffective_rollback_does_not_reset_policy_version_or_baseline(self):
        self.inv['balance']=30_000_000;self.fresh()
        old={'schema_version':2,'policy_contract_version':POLICY_VERSION,'score_version':VERSION,'version':'keep','config':{**DEFAULT,'window_budget':48_000_000},
             'previous_config':DEFAULT,'at':self.now-1,'expires_at':self.now+1000,
             'changed_at':1,'baseline':{'at':1,'book':{'book':123}}}
        path=self.root/'state/seller-meta-policy.json';path.write_text(json.dumps(old))
        r=meta.review(self.root,now=self.now,public=[],model=lambda *a:{**self.decision(),'action':'rollback'})
        self.assertEqual(r['status'],'ineffective_change_blocked')
        saved=json.loads(path.read_text())
        self.assertEqual(saved['version'],'keep');self.assertEqual(saved['changed_at'],1)
        self.assertEqual(saved['baseline'],old['baseline'])


if __name__=='__main__':unittest.main()
