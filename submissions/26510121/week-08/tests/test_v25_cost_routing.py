"""Low-cost routing must retain current evidence, independent review and money guards."""
import copy
import json
import pytest
from r3.state import policy
from r3.llm_roles import token_cost
from r3 import research
from test_market_cycle import fixture,FakeAPI


def test_cache_billing_categories_are_disjoint_and_reasoning_not_double_charged():
    cfg=policy();u={'input_tokens':1000,'output_tokens':200,
        'input_tokens_details':{'cached_tokens':300,'cache_write_tokens':400},
        'output_tokens_details':{'reasoning_tokens':150}}
    assert token_cost(cfg,'gpt-6-luna',u)==pytest.approx((300*.1+300*.01+400*.125+200*.5)/1e6)
    u['input_tokens_details']['cached_tokens']=2000
    assert token_cost(cfg,'gpt-6-luna',u)==pytest.approx((1000*.01+200*.5)/1e6)


def test_frozen_operating_usage_costs_less_than_half_with_independent_critic():
    cfg=policy()
    recorded=[('market_research_analyst','gpt-6.1-sol',47092,5748),
              ('market_research_critic','gpt-6.1-terra',49026,6208),
              ('capability_triage','gpt-6-luna',17772,1117),
              ('capability_designer','gpt-6.1-sol',6669,233)]
    old=new=0
    for role,model,inp,out in recorded:
        u={'input_tokens':inp,'output_tokens':out,'input_tokens_details':{'cache_write_tokens':inp}}
        old+=token_cost(cfg,model,u)
        new+=token_cost(cfg,cfg['background_llm_roles'][role]['model'],u)
    assert new<old*.5
    assert cfg['background_llm_roles']['market_research_critic']['model']=='gpt-6.1-terra'
    assert cfg['background_llm_roles']['market_research_critic']['max_output_tokens']==8192
    assert cfg['daily_api_budget_usd'] is None


def test_cache_routing_never_reuses_an_old_decision_or_weakens_evidence_enum(tmp_path):
    j,cfg,now=fixture(tmp_path);api=FakeAPI()
    for eid in ('current-one','current-two'):
        research.call(j,cfg,{'observed_at':now,'evidence_ids':[eid]},[eid],'analyst',client=api,key='synthetic')
    assert len(api.requests)==2
    a,b=api.requests
    assert a['prompt_cache_key']==b['prompt_cache_key']
    assert json.loads(a['input'])['valid_evidence_ids']!=json.loads(b['input'])['valid_evidence_ids']
    assert b['text']['format']['schema']['properties']['evidence_ids']['items']['enum']==['current-two']
    assert all(r['model']=='gpt-6-luna' for r in api.requests)
    assert all('priced_token_estimate_usd' in r for r in j.events('model_usage'))
    assert not j.events('purchase') and not j.events('shop_settled')


def test_cheap_analyst_cannot_bypass_separate_critic_or_mutate_config(tmp_path):
    j,cfg,now=fixture(tmp_path);before=copy.deepcopy(cfg);api=FakeAPI()
    a=research.call(j,cfg,{'evidence_ids':['now']},['now'],'analyst',client=api,key='synthetic')
    research.call(j,cfg,{'evidence_ids':['now']},['now'],'critic',proposal=a,client=api,key='synthetic')
    assert [r['model'] for r in api.requests]==['gpt-6-luna','gpt-6.1-terra']
    assert cfg==before and j.get('research-last-result') is None
    assert not any('astra' in r['model'] for r in api.requests)
