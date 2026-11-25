import time
import pytest
from r3.state import Journal,policy,atomic_json
from r3.policy_learning import update,packet,service_adjust,counter_probability
from r3.shop_growth import target_stock,allocate_velocity
from r3.market_memory import buyer_price_actions


def test_adaptive_horizon_control_changes_real_quantity():
    cfg=dict(policy(),shop_adaptive_demand_enabled=True,shop_inventory_caps_enabled=False)
    small=target_stock(10,4,dict(cfg,shop_demand_horizon_cycles=1),coverage_hours=.34)
    large=target_stock(10,4,dict(cfg,shop_demand_horizon_cycles=3),coverage_hours=.34)
    assert large>small


def test_service_loss_is_deduplicated_and_requires_paid_evidence(tmp_path):
    j=Journal(tmp_path);now=time.time();j.put('market_rules',{'sha256':'r'})
    for n in range(3):
        j.add('shop_opportunity',{'task':'paid'+str(n),'sku':'G1'},ts=now-500)
        j.add('shop_settled',{'task':'paid'+str(n),'sku':'G1'},ts=now-400)
    for minute in range(3):
        for n in range(30):
            j.add('stockout',{'sku':'G1','task':str(minute)+':'+str(n),'reason':'reserved_stock','eligible_units':1},ts=now-60*minute-1)
    p=update(j,policy(),now)
    assert p['service']['G1']['deduplicated_requests']==3
    assert p['service']['G1']['qualified']
    rate,evidence=service_adjust(j,'G1',.8,3.5,policy(),now)
    assert .8<rate<=4.3 and evidence['not_proven_lost_sales']
    assert service_adjust(j,'G1',.8,3.5,policy(),now+181)[0]==.8
    j.put('market_rules',{'sha256':'changed'})
    assert not packet(j,now)


def test_anonymous_burst_cannot_create_unlimited_demand(tmp_path):
    j=Journal(tmp_path);now=time.time()
    for n in range(200):j.add('stockout',{'sku':'G1','task':str(n),'reason':'reserved_stock','eligible_units':1},ts=now-1)
    row=update(j,policy(),now)['service']['G1']
    assert row['deduplicated_requests']==1 and not row['qualified']


def test_counter_learning_uses_completed_same_route_price_outcomes_only(tmp_path):
    j=Journal(tmp_path);now=time.time()
    for oid in ('paid','failed','transport','pending'):
        j.add('buyer_order',{'order':oid,'kind':'goods'},ts=now-100)
        j.add('buyer_counter',{'order':oid,'shop':'s','ask':1000,'price':800},ts=now-80)
    j.add('purchase',{'order':'paid','shop':'s','price':800},ts=now-60)
    j.add('attempt',{'order':'failed','shop':'s','kind':'no_agreement'},ts=now-60)
    j.add('attempt',{'order':'transport','shop':'s','kind':'ReadTimeout'},ts=now-60)
    model=update(j,policy(),now)['counter_completion_by_kind']['goods']
    assert model['16']=={'n':2,'paid':1}
    assert counter_probability(model,.8,.6)==pytest.approx(3.4/6)


def test_saved_bid_frontier_is_unique_monotone_and_never_a_cap():
    state={'metric':'saved','score_formula':'margin_share_v36','unit_cost':6500,'saved':.7,'deals':100}
    actions=buyer_price_actions(8000,10000,{'list_price':10000},8000,.92,0,4,
        saved_state=state,counter_model={},target_saved=[.8,.9])
    assert len({a['id'] for a in actions})==len(actions)
    assert any(a['id']=='accept' and a['price']==8000 for a in actions)
    assert {6850,7200}<=set(a['price'] for a in actions)
    later=buyer_price_actions(8000,10000,{'list_price':10000},8000,.92,1,4,previous_offer=7500,
        saved_state=state,counter_model={},target_saved=[.8,.9])
    assert all(a['price']>7500 for a in later)


def candidate(sku,cost,price):
    return {'sku':sku,'qty':1,'capital_commitment':cost,'max_cost':cost,'category':sku,
        'expected_delta':{'book':price-cost,'profit':price,'revenue':price},'score':price-cost,
        'evidence':{'arrival_rate_scenario':3,'planning_hours':6,'resale_price_scenario':price,
            'scenario_unit_cost':cost,'refund_stress':0,'kind':'goods','exploration':False,
            'growth':{},'expected_holding_hours':1}}


def test_abundant_cash_optimizes_supply_slot_book_instead_of_per_credit():
    cfg=dict(policy(),restock_max_lines=1,shop_inventory_caps_enabled=False)
    offers=[candidate('cheap',10,30),candidate('valuable',1000000,1000300)]
    old=allocate_velocity(offers,{}, {'spendable':10000000},dict(cfg,book_growth_v23_enabled=False),[],{})
    new=allocate_velocity(offers,{}, {'spendable':10000000},cfg,[],{})
    assert old[0]['sku']=='cheap' and new[0]['sku']=='valuable'
    assert new[0]['expected_delta']['book']>old[0]['expected_delta']['book']
    assert new[0]['evidence']['scarce_resource']=='supply_lines_and_time'
    constrained=allocate_velocity(offers,{}, {'spendable':500},cfg,[],{})
    assert sum(c['capital_commitment'] for c in constrained)<=500


def test_diagnostics_do_not_mix_old_failed_orders_with_current_policy(tmp_path):
    from r3.evolution import summarize
    j=Journal(tmp_path);now=time.time();j.put('buyer_revision_epoch',now-60)
    for n in range(20):
        j.add('buyer_order',{'order':str(n),'kind':'goods'},ts=now-100)
        j.add('market_result',{'order':str(n),'verified':False},ts=now-99)
    j.add('buyer_order',{'order':'new','kind':'goods'},ts=now-50)
    j.add('market_result',{'order':'new','verified':True},ts=now-40)
    j.add('purchase',{'order':'new'},ts=now-40)
    assert summarize(j,now)['goods']['conversion']==1
    assert summarize(j,now)['goods']['closed']==1


def test_offerable_inventory_does_not_count_reserved_units(tmp_path):
    from r3.merchant import observe_inventory
    j=Journal(tmp_path);now=time.time();j.reserve('u','task',180,now)
    observe_inventory(j,{'round':3,'units':[{'unit':'u','sku':'G1'}]},now)
    row=j.get('merchant_stock_timeline')[-1]
    assert row['counts']=={'G1':1} and row['offerable_counts']=={}


def test_shop_contract_model_values_full_refund_without_forcing_unprofitable_sale(tmp_path):
    from r3.shop_offer import target_prices,completion
    j=Journal(tmp_path)
    atomic_json(j.md/'board.json',{'buyers':[{'saved':.7,'deals':100}]})
    unit={'list_price':10000,'cost':6000}
    short={'failure_upper':.2,'covered_lower':.05};full={'failure_upper':.2,'covered_lower':.2}
    a,ea=completion(.5,unit,7000,short,j,0)
    b,eb=completion(.5,unit,7000,full,j,0)
    assert b>a and eb['expected_buyer_saved_scenario']>ea['expected_buyer_saved_scenario']
    assert target_prices(unit,full,j)


def test_incomplete_exact_rewards_are_not_reported_as_cohort_mean(tmp_path):
    from r3.objective_context import buyer_evidence
    j=Journal(tmp_path);now=time.time()
    j.put('market_rules',{'buyer_saved_formula':'margin_share_v36'})
    for oid in ('healthy','refunded'):
        j.add('purchase',{'order':oid,'kind':'goods','paid_at':now-25000,'score_formula':'margin_share_v36'},ts=now-25000)
        j.add('outcome',{'order':oid,'canonical':True,'matured':True,'refund_status':'confirmed',
            'score_formula':'margin_share_v36','saved_contribution':0 if oid=='refunded' else None})
    cohort=buyer_evidence(j,now)['mature_quality_cohorts']['goods']
    assert cohort['confirmed_saved_paid_mean'] is None
    assert cohort['known_subset_saved_mean']==0 and cohort['exact_reward_coverage']==.5


def test_customer_price_memory_is_scoped_to_scoring_regime(tmp_path):
    from r3.customer_memory import profile
    j=Journal(tmp_path);now=time.time();j.put('market_rules',{'buyer_score_epoch':now-50})
    for task,at,price in [('old',now-100,900),('new',now-10,600)]:
        j.add('customer_visit',{'task':task,'signer':'key:peer'},ts=at)
        j.add('customer_paid',{'task':task,'price':price,'list_price':1000,'category':'x'},ts=at+1)
    row=profile(j,'key:peer','x')
    assert row['paid_visits']==1 and row['median_paid_to_list_ratio']==.6


def test_qualified_reserved_pipeline_can_replenish_one_held_unit(tmp_path):
    from test_market_cycle import fixture
    from r3.economy import restock_candidates
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,shop_adaptive_demand_enabled=True)
    inv=j.snapshot('inventory.json');inv.update(restock={'open':True,'opens_at':now-1,'closes_at':now+180},
        units=[{'unit':'u','sku':'G111','kind':'food','cost':500,'expires_at':now+86400}])
    j.reserve('u','busy',180,now)
    j.put('policy-learning:v23',{'at':now,'expires_at':now+180,'rules_hash':'official',
        'service':{'G111':{'qualified':True,'incremental_paid_rate_scenario':0}}})
    j.put('merchant_context_v5',{'at':now,'demand_policy_id':'uncalibrated-neutral','service_policy_at':now,
        'coverage_weights':{},'skus':{'G111':{'arrival_rate_scenario':1,'paid_arrival_rate_scenario':1,
            'positive_paid_while_available':3,'service_loss_adjustment':{'qualified':True}}}})
    rows=restock_candidates(inv,j.snapshot('catalog.json'),[],j,cfg,now)
    assert rows and rows[0]['evidence']['reserved_pipeline_buffer']==1
    assert rows[0]['qty']>=1 and rows[0]['expected_delta']['book']>0
