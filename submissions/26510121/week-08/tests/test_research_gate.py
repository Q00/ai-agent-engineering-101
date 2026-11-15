import copy
import pytest
from r3 import research_gate as gate
from test_market_cycle import fixture,opinion

def example(tmp_path):
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,research_noop_gate_enabled=True)
    facts={'control':{'status':'observe_and_trade','reasons':[],'leader_gap':.2},
        'framework':{'issues':['stock_coverage']},'parameters':{'shop_inventory_caps_enabled':False}}
    p=opinion(['one']);p['approve_trial']=False
    return j,cfg,now,facts,p

def test_identical_proposal_has_one_hour_expiry_and_never_renews_policy(tmp_path):
    j,cfg,now,f,p=example(tmp_path);j.put('research-last-result',{'at':now-10,'expires_at':now+100})
    before=j.get('research-last-result');assert gate.reviewed(j,cfg,f,p,now) is None
    gate.remember(j,f,p,p,now)
    assert gate.reviewed(j,cfg,f,p,now+100) and gate.reviewed(j,cfg,f,p,now+3600) is None
    assert j.get('research-last-result')==before

@pytest.mark.parametrize('change',['rules','guard','issues','parameter','proposal','trial','gap'])
def test_new_material_condition_or_executable_proposal_requires_critic(tmp_path,change):
    j,cfg,now,f,p=example(tmp_path);gate.remember(j,f,p,p,now)
    f,p=copy.deepcopy(f),copy.deepcopy(p)
    if change=='rules':j.put('market_rules',{'sha256':'changed'})
    if change=='guard':f['control']['status']='defensive';f['control']['reasons']=['feed_stale']
    if change=='issues':f['framework']['issues'].append('buyer_price_gap')
    if change=='parameter':f['parameters']['shop_inventory_caps_enabled']=True
    if change=='proposal':p['operating_policy']['cover_cycles']=3
    if change=='trial':p['approve_trial']=True
    if change=='gap':f['control']['leader_gap']=.5
    assert gate.reviewed(j,cfg,f,p,now+100) is None

def test_presentation_and_clock_changes_do_not_trigger_paid_review(tmp_path):
    j,cfg,now,f,p=example(tmp_path);gate.remember(j,f,p,p,now)
    f['observed_at']=now+100;p['summary']='Different explanation, same executable proposal.'
    p['evidence_ids']=['new_current_reference']
    assert gate.reviewed(j,cfg,f,p,now+100)

def test_gate_is_opt_in_and_does_not_cache_trial_authority(tmp_path):
    j,cfg,now,f,p=example(tmp_path);p['approve_trial']=True
    gate.remember(j,f,p,p,now);assert not j.get(gate.KEY,{})
    p['approve_trial']=False;gate.remember(j,f,p,p,now)
    cfg['research_noop_gate_enabled']=False
    assert gate.reviewed(j,cfg,f,p,now) is None

def test_research_skips_only_critic_and_does_not_republish_on_same_proposal(tmp_path,monkeypatch):
    from r3 import research
    j,cfg,now=fixture(tmp_path)
    cfg=dict(cfg,research_noop_gate_enabled=True,agent_policy_selection_enabled=False,
        objective_decision_enabled=False,capability_llm_enabled=False,objective_feedback_enabled=False)
    research.analytics(j,now);calls=[];cycles=[2]
    def call(journal,config,facts,evidence,role,**kw):
        calls.append(role);value=opinion(evidence);value['approve_trial']=False
        value['operating_policy']['cover_cycles']=cycles[0]
        return value
    monkeypatch.setattr(research,'call',call)
    first=research.update(j,cfg,now)
    assert first and calls==['analyst','critic']
    j.put('research-last-attempt',{})
    assert research.update(j,cfg,now) is None
    assert calls==['analyst','critic','analyst'] and len(j.events('market_research_published'))==1
    assert j.get('research-last-result')==first and j.events('research_critic_skipped')
    cycles[0]=3;j.put('research-last-attempt',{})
    assert research.update(j,cfg,now)
    assert calls[-2:]==['analyst','critic']
