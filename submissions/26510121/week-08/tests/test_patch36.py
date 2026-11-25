"""Scoring version boundaries and hidden-cost decisions; no network or payments."""
import time
import pytest
from r3.state import Journal,atomic_json,policy
from r3.buyer_scoring import MARGIN,LEGACY,contribution,saved_price_cap,state,cost_scenario,score_inputs
from r3.control import audit_rules
from r3.market_memory import buyer_assessment,buyer_price_actions
from r3.outcomes import reconcile,latest


def market(tmp_path):
    j=Journal(tmp_path);cfg=policy()
    j.put('market_rules',{'buyer_metric':'saved','buyer_saved_formula':MARGIN,'buyer_forum_metric':'raw'})
    atomic_json(j.md/'board.json',{'round':3,'buyers':[{'name':'seochanit','saved':.708,'deals':14500}]})
    return j,cfg


def test_rule_audit_rejects_unknown_denominator_not_just_saved_label(tmp_path):
    j=Journal(tmp_path)
    prefix=('shops by book, revenue and stars; each further unit adds 0.2%; up to +50%; '
            'buyers by saved rate; ALL weighs them 0.5, 0.3, 0.1, 0.1; divided by deals + 100; '
            'counts -1, or 0 if refunded; ')
    old=audit_rules(j,prefix+'surplus / list price')
    new=audit_rules(j,prefix+'(list price - price) / (list price - unit cost), at most 1')
    unknown=audit_rules(j,prefix+'(list price - price) / (list price - unknown cost), at most 1')
    assert old['buyer_saved_formula']==LEGACY and old['buyer_compatible']
    assert new['buyer_saved_formula']==MARGIN and new['buyer_compatible']
    assert unknown['compatible'] and not unknown['buyer_compatible']
    assert len(j.events('buyer_score_formula_changed'))==3


def test_margin_share_changes_the_preferred_contract_and_caps_at_one():
    assert contribution(80,100,score_formula=MARGIN,unit_cost=70)>contribution(60,100,score_formula=MARGIN,unit_cost=30)
    assert contribution(80,100)<contribution(60,100)
    assert contribution(50,100,score_formula=MARGIN,unit_cost=70)==1
    assert contribution(80,100,1,0,score_formula=MARGIN,unit_cost=70)==-1
    assert contribution(80,100,1,1,score_formula=MARGIN,unit_cost=70)==0


@pytest.mark.parametrize('saved',[0,.4,.708,.95])
@pytest.mark.parametrize('failure',[0,.05,.2,.6])
@pytest.mark.parametrize('cost',[2500,5500,9000])
def test_price_cap_inverse_matches_new_capped_reward(saved,failure,cost):
    current={'score_formula':MARGIN,'unit_cost':cost,'saved':saved}
    for covered in [0,failure]:
        cap,target=saved_price_cap(10000,current,failure,covered)
        if cap:
            assert contribution(cap,10000,failure,covered,**score_inputs(current))>=target-1e-12
            if target<1:
                assert contribution(cap+1,10000,failure,covered,**score_inputs(current))<target+1e-12


@pytest.mark.parametrize('cost',[None,float('nan'),100,101,-1])
def test_undefined_or_nonpositive_margin_is_not_invented(cost):
    with pytest.raises(ValueError):contribution(80,100,score_formula=MARGIN,unit_cost=cost)


def test_peer_cost_claim_cannot_increase_our_bid_and_food_grade_is_conditional(tmp_path):
    j,cfg=market(tmp_path);p={'kind':'food','list_price':29300}
    a=buyer_assessment(p,{'budget':29300},{'grade':'A','cost':29000},j,cfg)
    b=buyer_assessment(p,{'budget':29300},{'grade':'A','cost':1},j,cfg)
    assert a['cap']==b['cap']==29300 and a['saved_reference_cap']>19000
    assert not a['saved_target_is_hard_cap']
    assert a['required_grade']=='A' and not a['cost_scenario']['exact']
    unknown=buyer_assessment(p,{'budget':29300},{},j,cfg)
    assert unknown['cap']==a['cap'] and unknown['saved_reference_cap']<a['saved_reference_cap']
    assert cost_scenario(p,'B')['unit_cost']==11720
    assert state(j,cfg)['score_formula']==MARGIN


def test_actions_use_new_score_and_keep_monotone_bids(tmp_path):
    j,cfg=market(tmp_path);p={'kind':'food','list_price':10000}
    a=buyer_assessment(p,{'budget':10000},{'grade':'A'},j,cfg)
    actions=buyer_price_actions(6500,a['cap'],p,6500,.92,0,4,saved_state=a['saved_state'])
    accept=next(x for x in actions if x['id']=='accept')
    assert accept['expected_delta']['saved']>0
    assert accept['score']==pytest.approx(contribution(6500,10000,**score_inputs(a['saved_state'])))
    counters=buyer_price_actions(7500,a['cap'],p,7000,.92,1,4,previous_offer=6000,saved_state=a['saved_state'])
    assert all(x['price']>6000 and x['price']<=a['cap'] for x in counters)


def test_old_refusal_memory_expires_by_formula_while_transport_facts_survive(tmp_path):
    from r3.contracts import history
    from r3.buyer_execution import route_context
    j,cfg=market(tmp_path);now=time.time()
    j.add('buyer_contract_result',{'shop_identity':'owner:peer','order':'old','sku':'G1','kind':'goods','class':'price'})
    j.add('buyer_contract_result',{'shop_identity':'owner:peer','order':'new','sku':'G1','kind':'goods','class':'price','score_formula':MARGIN})
    j.add('attempt',{'order':'old','shop':'s','sku':'G1','kind':'no_agreement'})
    j.add('attempt',{'order':'transport','shop':'s','sku':'G1','kind':'ReadTimeout'})
    assert [r['order'] for r in history(j,now+1)['groups'][('owner:peer','G1','goods')]]==['new']
    assert route_context(j,now+1)['routes'][('s','G1')]['n']==1


def test_healthy_hidden_cost_is_never_an_exact_reward_and_breaks_remain_exact(tmp_path):
    j,cfg=market(tmp_path);now=time.time()
    p={'order':'o','unit':'u','kind':'goods','paid_at':now-25000,'price':600,'value':1000,
       'warranty_hours':6,'score_formula':MARGIN,'initial_saved_contribution':None,'initial_saved_lower_scenario':.8}
    j.put('public_feed_caught_up_at',now);reconcile(j,[p],now)
    out=latest(j)['o'];assert out['canonical'] and out['saved_contribution'] is None
    assert out['saved_lower_scenario']==.8 and not out['score_exact']
    j.add('public_broken',{'unit':'u','order':'o','hours_after_sale':1,'refunded':600},ts=now)
    reconcile(j,[p],now+31)
    assert latest(j)['o']['saved_contribution']==0 and latest(j)['o']['score_exact']


def test_prepared_cache_version_changes_when_formula_changes(tmp_path):
    from r3.prepared import version
    j,cfg=market(tmp_path);old=version(j)
    j.put('market_rules',{'buyer_saved_formula':LEGACY})
    assert version(j)!=old


def test_new_experiment_does_not_compare_legacy_rewards(tmp_path):
    from r3.evolution import evaluate
    j,cfg=market(tmp_path);now=time.time()
    j.add('framework_order',{'order':'o','experiment':'e','kind':'goods','arm':'challenger','saved_at_assignment':.3},ts=now-25000)
    j.add('market_result',{'order':'o','verified':False},ts=now-24000)
    result=evaluate(j,{'experiment':'e','since':now-30000},now)
    assert result['stats']['challenger']['closed']==0 and not result['promote']


def test_joint_objective_can_buy_below_saved_average_for_a_larger_deal_rank_gain(tmp_path,monkeypatch):
    from r3.model import Chooser
    from r3.buyer_scoring import joint_actions,terms_delta
    j,cfg=market(tmp_path)
    atomic_json(j.md/'board.json',{'round':3,'buyers':[
        {'name':'seochanit','saved':.8,'deals':100,'forum':.9,'posts':20},
        {'name':'peer','saved':.9,'deals':101,'forum':.9,'posts':30},
        {'name':'lower','saved':.1,'deals':50,'forum':.8,'posts':10}]})
    current={'metric':'saved','score_formula':MARGIN,'unit_cost':5000,'saved':.8,'deals':100}
    changes=terms_delta(current,6050,10000)
    assert changes['saved']<0
    actions=joint_actions([{'id':'accept','price':6050,'score':.79,'execution_seconds':1,'expected_delta':changes}])
    chooser=Chooser(j,cfg)
    monkeypatch.setattr(chooser,'_call',lambda *a,**k:pytest.fail('no API in numeric execution'))
    selected=chooser.choose('buyer',actions,{'buyer_execution':True,'operation':'price'})
    assert selected['id']=='accept' and selected['objective_utility']>0


def test_joint_outside_option_prevents_purchase_when_overall_score_decreases(tmp_path):
    from r3.model import Chooser
    from r3.buyer_scoring import joint_actions,terms_delta
    j,cfg=market(tmp_path)
    atomic_json(j.md/'board.json',{'round':3,'buyers':[
        {'name':'seochanit','saved':.8,'deals':100,'forum':.9,'posts':20},
        {'name':'peer','saved':.81,'deals':10000,'forum':.9,'posts':30},
        {'name':'lower','saved':.79,'deals':50,'forum':.8,'posts':10}]})
    current={'metric':'saved','score_formula':MARGIN,'unit_cost':5000,'saved':.8,'deals':100}
    actions=joint_actions([{'id':'accept','price':9500,'score':.1,'execution_seconds':1,
                          'expected_delta':terms_delta(current,9500,10000)}])
    assert Chooser(j,cfg).choose('buyer',actions,{'buyer_execution':True,'operation':'price'})['id']=='continue_search'


def test_private_cost_is_not_recorded_as_exact_margin_reward(tmp_path):
    from review import review
    j,cfg=market(tmp_path)
    atomic_json(j.md/'catalog.json',{'G1':{'kind':'goods','list_price':10000}})
    review({'order':{'order':'o'},'receipt':{'price':6000,'cost':9999},'settled':{
        'sku':'G1','shop':'s','grade':'A','unit':'u','value':10000,'warranty_hours':6}},j)
    p=j.events('purchase',1)[0]
    assert p['initial_saved_contribution'] is None and p['initial_saved_lower_scenario']==pytest.approx(4000/4500)
    assert not p['cost_scenario']['exact'] and p['score_formula']==MARGIN


def test_cost_observer_updates_a_small_packet_and_main_respects_its_freshness(tmp_path):
    from r3.buyer_costs import update,scenario
    j,cfg=market(tmp_path);now=time.time();p={'list_price':10000,'kind':'goods'}
    atomic_json(j.md/'catalog.json',{'G1':p})
    for n in range(30):
        j.add('shop_settled',{'unit':str(n),'sku':'G1','cost':7400,'grade':'A'},ts=now-10)
    j.add('shop_settled',{'unit':'duplicate','sku':'G1','cost':float('nan'),'grade':'A'},ts=now-10)
    before=scenario(j,p,'A',now)
    packet=update(j,cfg,now)
    after=scenario(j,p,'A',now+1)
    assert packet['groups']['goods:A']['n']==30 and after['samples']==30
    assert after['expected_source']=='shrunk_own_cost_proxy'
    assert sum(after['cost_samples'])>sum(before['cost_samples'])
    assert not after['other_seller_cost_exact'] and after['probabilities_are_scenarios']
    assert scenario(j,p,'A',now+181)['expected_source']=='public_range_scenario_grid'
    j.put('market_rules',{'sha256':'new'})
    assert scenario(j,p,'A',now+1)['model_id'] is None


def test_expected_score_integrates_margin_shares_instead_of_using_mean_cost():
    values=[5500,6500,7500]
    result=contribution(7000,10000,score_formula=MARGIN,cost_samples=values)
    assert result==pytest.approx(sum(min(1,3000/(10000-c)) for c in values)/3)
    assert result!=pytest.approx(contribution(7000,10000,score_formula=MARGIN,unit_cost=6500))
