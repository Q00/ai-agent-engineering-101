import copy
import json
import threading
import time
import httpx
import pytest
from r3 import capability, research
from r3.llm_roles import profile, call_json, decision_text
from r3.model import ModelError
from r3.state import policy
from r3.cycle import MarketCycle
from test_market_cycle import fixture, opinion, FakeAPI


def test_production_role_models_and_reasoning_are_supported():
    cfg=policy()
    expected={'market_research_analyst':'gpt-6.1-sol','market_research_critic':'gpt-6.1-terra',
              'capability_triage':'gpt-6-luna','capability_designer':'gpt-6.1-sol'}
    for role,model in expected.items():
        assert profile(cfg,role)['model']==model
    assert cfg['actor_model']=='gpt-6-luna' and cfg['daily_api_budget_usd'] is None
    cfg=copy.deepcopy(cfg);cfg['background_llm_roles']['market_research_analyst']['reasoning']='none'
    with pytest.raises(ModelError,match='role_profile_invalid'):profile(cfg,'market_research_analyst')
    cfg['background_llm_roles']['market_research_analyst']['model']='gpt-6-astra'
    with pytest.raises(ModelError,match='role_profile_invalid'):profile(cfg,'market_research_analyst')


def test_analyst_and_critic_send_different_models_and_record_effort(tmp_path):
    j,cfg,now=fixture(tmp_path);api=FakeAPI();facts={'evidence_ids':['one']}
    a=research.call(j,cfg,facts,['one'],'analyst',client=api,key='synthetic')
    research.call(j,cfg,facts,['one'],'critic',proposal=a,client=api,key='synthetic')
    assert [r['model'] for r in api.requests]==['gpt-6.1-sol','gpt-6.1-terra']
    assert all(r['reasoning']['effort']=='medium' and r['max_output_tokens']==8192 for r in api.requests)
    assert all(r['text']['format']['schema']['properties']['evidence_ids']['items']['enum']==['one'] for r in api.requests)
    assert 'enum' not in research.SCHEMA['properties']['evidence_ids']['items']
    assert {r['reasoning_effort'] for r in j.events('model_usage')}=={'medium'}


class CapabilityAPI:
    def __init__(self,broken=False):self.requests=[];self.broken=broken
    def post(self,url,**kw):
        b=kw['json'];self.requests.append(b);e=json.loads(b['input'])['valid_evidence_ids']
        if len(self.requests)==1:
            value={'design_needed':True,'focus':'stock_depth_audit','reason':'Compare depth and blockers.','evidence_ids':e[:2]}
        else:
            value={'tools':['stock_depth_audit','eligibility_intervals'],'reason':'Calculate supported gaps.','evidence_ids':e[:2]}
            if self.broken:value['tools']=['shell']
        output=([{'type':'function_call','name':'submit_background_decision','arguments':json.dumps(value)}]
            if b.get('tools') else [{'type':'message','phase':'final_answer','content':[{'type':'output_text','text':json.dumps(value)}]}])
        return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':50,'output_tokens':100},'output':output})


def test_tool_designer_produces_facts_without_policy_or_payment_authority(tmp_path):
    j,cfg,now=fixture(tmp_path);research.analytics(j,now);api=CapabilityAPI()
    old_config=copy.deepcopy(cfg);before=j.get('research-last-result')
    result=capability.update(j,cfg,now,client=api,key='synthetic')
    assert len(api.requests)==2 and result['authority']=='read_only_calculated_evidence'
    assert result['calculations']['stock_depth_audit']['repeat_skus'][0]['observed_paid20']==10
    interval=result['calculations']['eligibility_intervals']['rows'][0]
    assert interval['reference_price_min']==pytest.approx(1000/1.5)
    assert interval['reference_price_max']==2000
    assert cfg==old_config and j.get('research-last-result')==before and not j.events('purchase')
    assert api.requests[1]['tool_choice']=={'type':'function','name':'submit_background_decision'}
    assert api.requests[1]['parallel_tool_calls'] is False
    assert j.get('capability-plan-v19')['effects']=='read_only_analysis'
    assert capability.context(j,now+181) is None
    j.put('market_rules',{'sha256':'changed'})
    assert capability.context(j,time.time()) is None


def test_unknown_generated_tool_is_rejected_and_old_facts_are_retained(tmp_path):
    j,cfg,now=fixture(tmp_path);research.analytics(j,now)
    assert capability.update(j,cfg,now,client=CapabilityAPI(True),key='synthetic') is None
    assert j.get('capability-plan-v19') is None and j.events('capability_deferred')
    assert not capability.valid({'tools':['shell'],'reason':'x','evidence_ids':['x']},['x'],True)
    assert not capability.valid({'tools':['stock_depth_audit'],'reason':'x','evidence_ids':['invented']},['x'],True)


def test_capability_brief_is_used_by_research_not_just_logged(tmp_path):
    j,cfg,now=fixture(tmp_path);research.analytics(j,now)
    assert capability.update(j,cfg,now,client=CapabilityAPI(),key='synthetic')
    api=FakeAPI();assert research.update(j,cfg,time.time(),client=api,key='synthetic')
    payload=json.loads(api.requests[0]['input'])
    brief=payload['facts']['capability_evidence']
    assert 'capability:'+brief['id'] in payload['valid_evidence_ids']
    assert 'stock_depth_audit' in brief['calculations']
    assert any(r['capability_context_id']==brief['id'] for r in j.events('background_model_request'))
    assert len(api.requests[0]['input'].encode())<32000


def test_slow_or_changed_source_review_cannot_publish_as_fresh(tmp_path):
    j,cfg,now=fixture(tmp_path);a=opinion(['one'])
    assert research.publish(j,cfg,{'observed_at':now-181,'evidence_ids':['one']},a,a,'official',now) is None
    assert not j.events('market_research_published')


def test_incomplete_or_transport_failure_never_retries_or_silently_switches_model(tmp_path):
    j,cfg,now=fixture(tmp_path)
    class API:
        calls=0
        def post(self,*a,**kw):
            self.calls+=1
            return httpx.Response(200,json={'status':'incomplete','usage':{'input_tokens':100,'output_tokens':8192},'output':[]})
    api=API()
    with pytest.raises(ModelError,match='research_incomplete'):
        research.call(j,cfg,{'evidence_ids':['one']},['one'],'analyst',client=api,key='synthetic')
    assert api.calls==1 and len(j.events('model_usage'))==1
    assert j.events('model_usage')[0]['automatic_model_fallback'] is False


def test_capability_lane_cannot_block_preparation_research_or_supply(tmp_path,monkeypatch):
    j,cfg,now=fixture(tmp_path);cycle=MarketCycle(j,cfg,None,None)
    barrier=threading.Event();entered=threading.Event();prepared=threading.Event();researched=threading.Event()
    def slow():entered.set();barrier.wait(2)
    monkeypatch.setattr(cycle,'capabilities',slow)
    monkeypatch.setattr(cycle,'prepare',prepared.set)
    monkeypatch.setattr(cycle,'research',researched.set)
    try:
        start=time.perf_counter();cycle.tick(now)
        assert time.perf_counter()-start<.2
        assert entered.wait(1) and prepared.wait(1) and researched.wait(1)
        assert cycle.capability_lane.busy and not cycle.supply_lane.busy
    finally:barrier.set();cycle.stop.set()


@pytest.mark.parametrize('calls',[
    [],
    [{'type':'function_call','name':'shell','arguments':'{}'}],
    [{'type':'function_call','name':'submit_background_decision','arguments':'{}'}]*2,
    [{'type':'function_call','name':'submit_background_decision','status':'in_progress','arguments':'{}'}]])
def test_forced_function_rejects_missing_unknown_duplicate_or_incomplete_calls(calls):
    with pytest.raises(ModelError,match='decision_function_contract_invalid'):
        decision_text({'output':calls},'function')
