import copy
import time
import pytest
from r3.state import Journal, atomic_json, identity, policy
from r3 import feedback, contracts, quality
from r3.model import Chooser
from r3.shop_growth import target_stock


def market(tmp_path):
    now=time.time();j=Journal(tmp_path);cfg=dict(policy(),objective_feedback_enabled=True,shop_inventory_caps_enabled=True,shop_adaptive_demand_enabled=False,shop_internal_capital_caps_enabled=True,shop_reputation_v24_enabled=False)
    j.put('market_rules',{'sha256':'rules','compatible':True,'buyer_compatible':True})
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    atomic_json(j.md/'board.json',{'round':3,'shops':[
        {'name':'seochanit','all':.5,'book':2000000,'deals':100,'revenue':4000000,'stars':3,'stars_rank':3,'reviews':100,'stock_units':2,'alive':True},
        {'name':'peer','all':.7,'book':3000000,'deals':200,'stock_units':100,'alive':True}],
        'buyers':[{'name':'seochanit','all':.5,'saved':.3,'deals':100,'posts':10,'forum':.8}]})
    atomic_json(j.md/'inventory.json',{'round':3,'balance':5000000,'units':[{'unit':'u','sku':'G1'}]})
    j.put('market_control',{'pause_restock':False,'cash_capacity':{'spendable':4000000}})
    j.put('cycle-market-analytics',{'at':now,'windows':{'20':{'trades':100},'60':{'trades':300}}})
    j.put('research-last-result',{'at':now,'expires_at':now+1200,'rules_hash':'rules','trial_review_approved':True,
        'operating_intents':['stock_coverage','buyer_price_gap']})
    for n in range(8):
        j.add('shop_settled',{'unit':'sold'+str(n),'task':'task'+str(n),'sku':'G1','price':900,'cost':500},ts=now-100+n)
    for n in range(3):j.add('restock_blocked',{'reason':'market_shop_not_discoverable'},ts=now-60+n)
    for n in range(30):
        j.add('buyer_order',{'order':str(n),'kind':'goods'},ts=now-80+n)
        j.add('market_result',{'order':str(n),'verified':False},ts=now-60+n)
    return j,cfg,now


def test_reviewed_analysis_changes_candidate_parameters_before_main_decides(tmp_path):
    j,cfg,now=market(tmp_path);packet=feedback.update(j,cfg,now)
    shop=feedback.apply(j,cfg,'shop',now=now);buyer=feedback.apply(j,cfg,'buyer',order='new',now=now)
    assert shop['shop_demand_horizon_cycles']>1 and buyer['buyer_frontier_category_enabled']
    assert target_stock(12,8,shop)>target_stock(12,8,cfg)
    assert buyer['buyer_saved_target_margin']==cfg['buyer_saved_target_margin']
    assert shop['restock_max_qty']==20 and shop['daily_api_budget_usd'] is None
    candidates=[{'id':'safe','score':1,'expected_delta':{'saved':.01,'deals':1}},
                {'id':'bad','score':-1,'expected_delta':{'saved':-.01,'deals':0}}]
    selected=Chooser(j,buyer).choose('buyer',candidates,{'order':{'order':'new'},'buyer_execution':True,'operation':'offer_selection'})
    assert selected['id']=='safe'
    receipt=j.events('feedback_main_choice')[0]
    assert receipt['policy_id']==packet['id'] and receipt['chosen']=='safe' and receipt['order']=='new'
    j.add('purchase',{'order':'new'},ts=now+1);j.add('market_result',{'order':'new','verified':True},ts=now+1)
    # Its order belongs to the next measurement window, not an invented result.
    j.add('buyer_order',{'order':'new','kind':'goods'},ts=now)
    j.put('board_checked_at',now+61);j.put('public_feed_caught_up_at',now+61)
    result=feedback.update(j,cfg,now+61)
    assert result['execution_feedback']['buyer_closed_results_by_policy'][packet['id']]['paid']==1


@pytest.mark.parametrize('fault',['stale','future','rules','corrupt','feed','forbidden_patch'])
def test_stale_forged_and_unsafe_policies_never_actuate(tmp_path,fault):
    j,cfg,now=market(tmp_path);feedback.update(j,cfg,now)
    p=j.snapshot(feedback.FILE)
    if fault=='stale':p['at']=now-151
    if fault=='future':p['at']=now+1
    if fault=='rules':j.put('market_rules',{'sha256':'changed'})
    if fault=='corrupt':p['checksum']='bad'
    if fault=='feed':j.put('public_feed_caught_up_at',now-200)
    if fault=='forbidden_patch':p['patches']['buyer']['buyer_saved_target_margin']=-1
    if fault!='corrupt':p['checksum']=identity({k:v for k,v in p.items() if k!='checksum'})
    atomic_json(j.md/feedback.FILE,p)
    assert feedback.apply(j,cfg,'shop',now=now)==cfg


def test_operating_policy_freezes_per_order_and_semantic_id_does_not_churn(tmp_path):
    j,cfg,now=market(tmp_path);first=feedback.update(j,cfg,now)
    a=feedback.apply(j,cfg,'buyer',order='one',now=now)
    j.put('board_checked_at',now+61);j.put('public_feed_caught_up_at',now+61)
    second=feedback.update(j,cfg,now+61)
    assert first['id']==second['id']
    j.put('research-last-result',{})
    j.put('board_checked_at',now+122);j.put('public_feed_caught_up_at',now+122)
    feedback.update(j,cfg,now+122)
    assert feedback.apply(j,cfg,'buyer',order='one',now=now+122)==a
    assert not feedback.apply(j,cfg,'buyer',order='two',now=now+122).get('buyer_frontier_category_enabled')


def test_new_market_shock_reduces_coverage_and_peer_trends_are_numeric(tmp_path):
    j,cfg,now=market(tmp_path);feedback.update(j,cfg,now-1201)
    old=j.snapshot('board.json');old['shops'][1]['book']=1000000
    j.add('feedback_market_observation',{'peers':{'shops':old['shops'],'buyers':old['buyers']}},ts=now-1201)
    j.put('cycle-market-analytics',{'at':now,'windows':{'20':{'trades':1000},'60':{'trades':1100}}})
    packet=feedback.update(j,cfg,now)
    assert packet['regime']=='shock' and packet['patches']['shop']['shop_demand_horizon_cycles']<=1.5
    assert packet['peer_trends']['shops'][0]['book_per_hour']>0


def failed_frontier(j,now):
    j.put('market-shop-identities',{'old':{'owner':'same'},'new':{'owner':'same'},'other':{'owner':'other'}})
    for n in range(8):
        j.add('buyer_contract_result',{'order':str(n),'kind':'goods','shop_identity':'owner:same',
            'sku':'G'+str(n%3),'category':'goods>chairs','class':'price','ask':900,'cap':500,
            'list_price':1000,'grade':'A','warranty_hours':6,'signature':str(n)},ts=now-60+n)
    contracts._HISTORY.clear();contracts._IDENTITIES.clear()


def route(shop='new',price=900):
    return {'shop':shop,'sku':'G9','reference_price':price,'product':{'list_price':1000,'category':'goods>chairs'},
        'evidence':{'seller_term_prior':{'grade':'A','warranty_hours':6}}}


def test_category_frontier_remembers_across_skus_and_urls_without_starving_recovery(tmp_path):
    j,cfg,now=market(tmp_path);failed_frontier(j,now)
    cfg=dict(cfg,system_framework_enabled=True,buyer_frontier_category_enabled=True,buyer_contract_probe_percent=0)
    assert len(contracts.prepare([route()],{'order':'probe','kind':'goods'},j,cfg))==1
    assert contracts.prepare([route()],{'order':'next','kind':'goods'},j,cfg)==[]
    assert len(contracts.prepare([route(price=600)],{'order':'lower-price','kind':'goods'},j,cfg))==1
    assert len(contracts.prepare([route(shop='other')],{'order':'other-owner','kind':'goods'},j,cfg))==1
    j.add('purchase',{'order':'recovered','shop':'old','sku':'G0','kind':'goods'},ts=now-.001)
    contracts._HISTORY.clear()
    assert len(contracts.prepare([route()],{'order':'after-paid','kind':'goods'},j,cfg))==1


def test_quality_model_needs_verified_holdout_and_never_changes_price_guards(tmp_path):
    j,cfg,now=market(tmp_path);atomic_json(j.md/'catalog.json',{'G1':{'list_price':1000}})
    for n in range(180):
        cheap=n%2==0
        j.add('shop_settled',{'unit':'q'+str(n),'sku':'G1','grade':'A','warranty_hours':6,
            'price':500 if cheap else 950,'cost':400},eid='settlement:'+str(n),ts=now-1000+n)
        j.add('shop_review_observed',{'ledger':n,'stars':5 if cheap else 1,'author':'owner:'+str(n%13),
            'source':'verified_market_result_and_accepted_own_task','identity_known':True},ts=now-990+n)
    model=quality.update(j,cfg,now)
    assert model['summary']['mode']=='validated_predictive_surrogate'
    options=[{'id':'q','action':'counter','price':500,'warranty_hours':6,'recovery_floor':450,'expected_delta':{'book':100},'evidence':{}},
        {'id':'hold','action':'hold','price':0,'expected_delta':{'book':0},'evidence':{}}]
    quality.augment(j,cfg,{'grade':'A','list_price':1000},options,now)
    assert options[0]['expected_delta']['stars_rank']>0 and options[0]['price']==500 and options[0]['recovery_floor']==450
    assert 'stars_rank' not in options[1]['expected_delta']
    j.put('board_checked_at',now-200)
    fresh=copy.deepcopy(options);fresh[0]['expected_delta'].pop('stars_rank')
    quality.augment(j,cfg,{'grade':'A','list_price':1000},fresh,now)
    assert 'stars_rank' not in fresh[0]['expected_delta']


def test_nonpredictive_reviews_stay_shadow(tmp_path):
    j,cfg,now=market(tmp_path)
    assert quality.update(j,cfg,now)['summary']['mode']=='shadow'


def test_external_paid_demand_breaks_the_no_stock_no_sales_cold_start_loop(tmp_path):
    j,cfg,now=market(tmp_path)
    m=j.get('cycle-market-analytics');m['top_skus']=[{'sku':'Gnew','recent20':30,'recent60':50,'buyers20':5}]
    j.put('cycle-market-analytics',m)
    packet=feedback.update(j,cfg,now);effective=feedback.apply(j,cfg,'shop',now=now)
    assert packet['patches']['shop']['shop_public_demand_skus']==['Gnew']
    # Same estimated obtainable arrival rate; no competition stock copying.
    assert target_stock(18,0,effective,public_qualified=True)>target_stock(18,0,cfg)
    assert target_stock(1000,0,effective,public_qualified=True)==6
    assert target_stock(1000,8,effective)==20


def test_operating_response_is_separate_from_unproven_economic_trial(tmp_path):
    from r3.research import publish
    from test_market_cycle import opinion
    j,cfg,now=market(tmp_path)
    evidence=['rules:rules','kv:feedback-last-policy']
    analyst=opinion(evidence);critic=opinion(evidence)
    analyst['approve_trial']=False;critic['approve_trial']=False
    result=publish(j,cfg,{'evidence_ids':evidence},analyst,critic,'rules',now)
    assert result['trial_review_approved'] is False and result['queued_buyer_profile'] is None
    assert result['operating_intents']==['buyer_price_gap','stock_coverage']
    assert feedback.update(j,cfg,now)['patches']['buyer']['buyer_frontier_category_enabled']


def test_execution_context_is_bounded_without_losing_observed_totals():
    from r3.research import execution_projection
    rows={str(n):n for n in range(1000)}
    result=execution_projection({'main_choices_by_policy':rows,
        'buyer_closed_results_by_policy':{k:{'closed_orders':v,'paid':v//2} for k,v in rows.items()}})
    assert result['main_choices_total']==sum(rows.values())
    assert len(result['main_choices_by_policy'])==4
    assert len(result['buyer_closed_results_by_policy'])==4
