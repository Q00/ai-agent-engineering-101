import copy
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from r3.state import atomic_json, identity, Journal, policy
from r3 import research, feedback
from r3.objective_context import curate, buyer_evidence, observe_forum, shop_alternatives, FACT_BYTES
from test_market_cycle import fixture, opinion, FakeAPI


def test_thousands_of_policy_cohorts_cannot_stop_either_research_call(tmp_path):
    j,cfg,now=fixture(tmp_path)
    cohorts={str(i):{'closed_orders':100,'paid':7} for i in range(2000)}
    meta={'operating_policy':{'id':'current','execution_feedback':{'buyer_closed_results_by_policy':cohorts}},
          'issues':['price_frontier_no_overlap']}
    facts={'evidence_ids':['one'],'meta':meta,
           'operating_feedback':{'execution_feedback':research.execution_projection({'buyer_closed_results_by_policy':cohorts})},
           'current_operating_policy':{'cover_cycles':1,'initial_batch_limit':6,'frontier_min_orders':8,'recheck_seconds':120}}
    api=FakeAPI()
    first=research.call(j,cfg,facts,['one'],'analyst',client=api,key='synthetic')
    research.call(j,cfg,facts,['one'],'critic',proposal=first,client=api,key='synthetic')
    assert len(api.requests)==2
    for body in api.requests:
        assert len(body['input'].encode())<32000
        actual=json.loads(body['input'])['facts']
        assert 'execution_feedback' not in actual['meta']['operating_policy']
        assert actual['operating_feedback']['execution_feedback']['buyer_closed_results_by_policy_total']==200000
    assert facts['meta']==meta  # Original audit evidence is not compacted in place.


def test_context_budget_preserves_objective_alternatives_guards_and_evidence():
    facts={'evidence_ids':['rules:r'],'current_operating_policy':{'cover_cycles':1},
           'objective_decisions':{'id':'d','shop':{'spendable':500000000,'execution_permitted':False,
               'alternatives':[{'cover_cycles':n,'expected_book_scenario':n*100} for n in (1,2,3)]}},
           'market':{'buyer_preferences':[{'text':'untrusted '*2000} for _ in range(300)]},
           'meta':{'operating_policy':{'execution_feedback':{str(n):'x'*10000 for n in range(100)}}}}
    row,metrics=curate(facts)
    assert metrics['fact_bytes']<=FACT_BYTES and metrics['source_bytes']>1000000
    assert row['objective_decisions']==facts['objective_decisions']
    assert row['evidence_ids']==['rules:r'] and row['current_operating_policy']=={'cover_cycles':1}


def test_coverage_comparison_changes_depth_without_mutating_live_control(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.put('market_control',{'at':now,'pause_restock':True,'reasons':['market_shop_not_discoverable']})
    j.put('merchant_context_v5',{'at':now,'coverage_weights':{},'skus':{'G111':{
        'arrival_rate_scenario':5,'paid_arrival_rate_scenario':5,'positive_paid_while_available':8}}})
    before=j.get('market_control');inv=j.snapshot('inventory.json')
    row=shop_alternatives(j,cfg,now)
    assert row['execution_permitted'] is False
    assert row['spendable']>0 and row['alternatives'][-1]['units']>row['alternatives'][0]['units']
    assert all(r['capital_commitment']<=row['spendable'] for r in row['alternatives'])
    assert j.get('market_control')==before and j.snapshot('inventory.json')==inv


@pytest.mark.parametrize('refund_status,matured,canonical,n',[('confirmed',True,True,1),
    ('expected_by_warranty_rule',True,True,0),('none',False,True,0),('confirmed',True,False,0)])
def test_buyer_feedback_never_counts_pending_quality_as_final(tmp_path,refund_status,matured,canonical,n):
    j,cfg,now=fixture(tmp_path)
    j.add('buyer_order',{'order':'o','kind':'goods'},ts=now-100)
    j.add('market_result',{'order':'o','verified':True},ts=now-50)
    j.add('purchase',{'order':'o','initial_saved_contribution':.8,'price':1000000},ts=now-50)
    j.add('outcome',{'order':'o','canonical':canonical,'matured':matured,'refund_status':refund_status,
                     'saved_contribution':0},ts=now-1)
    row=buyer_evidence(j,now)['segments_60min']['goods']
    assert row['confirmed_mature_paid_n']==n and row['initial_saved_paid_mean']==.8
    assert row['quality_pending']==1-n


def test_forum_quality_requires_returned_post_identity_and_maturity(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.add('forum_delivery',{'role':'buyer','status':201,'post_id':10,'information_kind':'same_terms_price'})
    atomic_json(j.md/'forum.json',[{'id':10,'ts':now-700,'author':'seochanit','role':'buyer','score':.8},
        {'id':11,'ts':now-700,'author':'seochanit','role':'buyer','score':1},
        {'id':12,'ts':now-700,'author':'someone_else','role':'buyer','score':1}])
    observe_forum(j,now);observe_forum(j,now)
    assert len(j.events('forum_objective_observation'))==1
    row=buyer_evidence(j,now)['forum_formats']['same_terms_price']
    assert row['scored_posts_10min_old']==1 and row['mean_observed_score']==.8
    assert row['enough_for_descriptive_comparison'] is False


def test_forum_focus_is_reviewed_then_reaches_the_actual_scheduler(tmp_path,monkeypatch):
    from test_objective_feedback import market
    from r3.buyer_forum import schedule
    from r3 import buyer_insights
    j,cfg,now=market(tmp_path);cfg=dict(cfg,objective_decision_enabled=True)
    a=opinion(['one']);a['forum_focus']='acceptable_alternatives'
    a['operating_actions'].append('buyer_information')
    packet=research.publish(j,cfg,{'evidence_ids':['one']},a,a,'rules',now)
    compiled=feedback.update(j,cfg,now)
    assert compiled['patches']['buyer']['buyer_forum_focus']=='acceptable_alternatives'
    bucket=int(now//300)
    if bucket%5==0:now+=300
    # Keep the expiring compiled policy current at the selected legal slot.
    compiled['at']=now;compiled['expires_at']=now+150
    compiled['checksum']=identity({k:v for k,v in compiled.items() if k!='checksum'})
    atomic_json(j.md/feedback.FILE,compiled)
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    monkeypatch.setattr(buyer_insights,'alternatives',lambda *a:iter([('proof','observed alternatives',[1,2])]))
    schedule(j,cfg,{}, {},now)
    assert j.pending_posts()[0][1]['information_kind']=='acceptable_alternatives'
    assert j.events('buyer_forum_policy_choice')[0]['policy_id']==compiled['id']
    assert packet['economic_superiority_verified'] is False


def test_independent_review_disagreement_uses_balanced_information(tmp_path):
    j,cfg,now=fixture(tmp_path);a=opinion(['one']);b=opinion(['one'])
    a['forum_focus']='mature_quality';b['forum_focus']='same_terms_price'
    assert research.publish(j,cfg,{'evidence_ids':['one']},a,b,'official',now)['forum_focus']=='balanced'


@pytest.mark.parametrize('bad',['invented','vote_exchange',None,True,20])
def test_invalid_information_strategy_is_never_executable(bad):
    a=opinion(['one']);a['forum_focus']=bad
    assert not research.validate(a,['one'])
    assert not feedback.valid_patch('buyer',{'buyer_forum_focus':bad})


def test_stock_refusal_distinguishes_shelf_grade_and_reservation(tmp_path):
    from seller_r3 import Seller
    j,cfg,now=fixture(tmp_path)
    inv=j.snapshot('inventory.json')
    u={'unit':'u','sku':'G111','name':'food','category':'food>one','kind':'food','grade':'C',
       'expires_at':now+3600,'supplied_at':now-20,'cost':500,'list_price':1000}
    inv['units']=[u];atomic_json(j.md/'inventory.json',inv)
    seller=Seller(j)
    assert seller.select_unit('G111 must last at least 20 hours','t1') is None
    assert j.events('stockout')[0]['reason']=='shelf_life'
    assert seller.select_unit('G111 require grade A','t2') is None
    assert j.events('stockout')[0]['reason']=='grade_requirement'
    assert seller.select_unit('G111','t3')
    assert seller.select_unit('G111','t4') is None
    assert j.events('stockout')[0]['reason']=='reserved_stock'


def test_local_supply_guard_refuses_without_supplier_post_or_retry_sleep(tmp_path):
    from r3.adapter import load_kit, install
    j,cfg,now=fixture(tmp_path);kit=load_kit();calls=[]
    inv=j.snapshot('inventory.json');inv['restock']={'open':True,'opens_at':now-10,'closes_at':now+170}
    def original(self,method,path,role='buyer',**kw):
        calls.append((method,path));assert method=='GET' and path=='/api/inventory'
        return httpx.Response(200,json=inv)
    kit.Market.call=original
    install(kit,j,cfg)
    j.put('market_control',{'at':now,'pause_restock':True})
    response=kit.Market.call(object(),'POST','/api/supply','shop',json={'sku':'G111','qty':1})
    assert response.status_code==409 and 'wait' not in response.json()
    assert calls==[('GET','/api/inventory')]


def test_main_buyer_saved_choice_remains_without_any_model_request(tmp_path):
    from r3.model import Chooser
    j,cfg,now=fixture(tmp_path)
    atomic_json(j.md/'board.json',{'round':3,'buyers':[{'name':'seochanit','saved':.3,'deals':100,'forum':.8}]})
    j.put('market_rules',{'buyer_metric':'saved','buyer_forum_metric':'raw'})
    cfg=dict(cfg,buyer_patch34_enabled=True,objective_decision_enabled=True)
    class NoAPI:
        def post(self,*a,**k):pytest.fail('Main must not wait for a model')
    choice=Chooser(j,cfg,NoAPI()).choose('buyer',[
        {'id':'positive','score':1,'expected_delta':{'saved':.001,'deals':1}},
        {'id':'bad','score':-1,'expected_delta':{'saved':-.001,'deals':1}}],{'buyer_execution':True})
    assert choice['id']=='positive' and not j.events('model_usage')


@pytest.mark.parametrize('unsafe',[False,True])
def test_verbose_explanation_is_bounded_but_unsafe_policy_is_still_rejected(tmp_path,unsafe):
    j,cfg,now=fixture(tmp_path)
    class Verbose:
        def post(self,*a,**kw):
            value=opinion(['one']);value['summary']='long explanation '*200
            value['hypotheses']=['long hypothesis '*100]
            if unsafe:value['operating_policy']['cover_cycles']=999
            return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':1,'output_tokens':1},
                'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(value)}]}]})
    if unsafe:
        from r3.model import ModelError
        with pytest.raises(ModelError,match='research_contract_invalid'):
            research.call(j,cfg,{'evidence_ids':['one']},['one'],'analyst',client=Verbose(),key='synthetic')
    else:
        value=research.call(j,cfg,{'evidence_ids':['one']},['one'],'analyst',client=Verbose(),key='synthetic')
        assert len(value['summary'])==800 and len(value['hypotheses'][0])==400
    assert j.events('research_explanation_compacted')


def test_final_structured_decision_is_not_concatenated_with_commentary():
    from r3.model import response_text, ModelError
    def message(text,phase):
        return {'type':'message','phase':phase,'content':[{'type':'output_text','text':text}]}
    response={'status':'completed','output':[message('intermediate text with {untrusted data}','commentary'),
        message('{"chosen":"safe"}','final_answer')]}
    assert json.loads(response_text(response))=={'chosen':'safe'}
    response['status']='incomplete'
    with pytest.raises(ModelError,match='incomplete'):response_text(response)


@pytest.mark.parametrize('phases',[['commentary'],[None,None],['final_answer','final_answer']])
def test_ambiguous_or_commentary_only_responses_cannot_select_policy(phases):
    from r3.model import response_text, ModelError
    data={'status':'completed','output':[{'type':'message','phase':p,
        'content':[{'type':'output_text','text':'{"chosen":"unsafe"}'}]} for p in phases]}
    with pytest.raises(ModelError,match='missing_or_ambiguous'):response_text(data)


def test_six_hour_goods_results_are_observed_outside_the_recent_order_window(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.add('purchase',{'order':'old','kind':'goods','paid_at':now-25000},ts=now-25000)
    j.add('outcome',{'order':'old','canonical':True,'matured':True,'saved_contribution':-.5,
                     'refund_status':'uninsured'},ts=now-1)
    evidence=buyer_evidence(j,now)
    assert evidence['segments_60min']['goods']['paid']==0
    mature=evidence['mature_quality_cohorts']['goods']
    assert mature['confirmed_paid']==1 and mature['confirmed_saved_paid_mean']==-.5
