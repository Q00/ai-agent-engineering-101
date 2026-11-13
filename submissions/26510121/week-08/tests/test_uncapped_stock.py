import copy
import math
import time
from r3.state import policy,atomic_json
from r3.shop_growth import inventory_limit,target_stock,poisson_tail,marginal,allocate_championship,demand_diagnostics
from r3.economy import restock_candidates
from r3 import prewindow
from test_market_cycle import fixture
from test_shop_velocity import candidate


def test_live_configuration_removes_inventory_caps_not_market_lot_limit():
    cfg=policy()
    assert cfg['shop_inventory_caps_enabled'] is False
    assert inventory_limit(cfg) is None
    assert cfg['restock_max_qty']==20 and cfg['restock_max_lines']==10
    assert cfg['background_llm_roles']['market_research_analyst']['model']=='gpt-6-luna'


def test_demand_targets_can_exceed_twenty_and_sparse_fixed_batch_limits():
    cfg=policy()
    assert target_stock(1000,8,cfg)>20
    assert target_stock(1000,0,cfg,True,public_limit=6)>6
    assert target_stock(1000,0,cfg)>2
    assert target_stock(0,8,cfg)==1
    legacy=dict(cfg,shop_inventory_caps_enabled=True)
    assert target_stock(1000,8,legacy)==20
    assert target_stock(1000,0,legacy)<=2


def test_poisson_probabilities_remain_finite_above_old_underflow_threshold():
    assert .5<poisson_tail(1000,1000)<.51
    assert poisson_tail(1000,1100)<poisson_tail(1000,1050)<poisson_tail(1000,1000)
    assert poisson_tail(1000,100)<1.0000000001
    assert poisson_tail(1000,100)>1-1e-10
    assert abs(poisson_tail(1,1)-(1-math.exp(-1)))<1e-12


def test_current_plan_can_add_inventory_above_old_total_and_sku_caps():
    cfg=policy();c=candidate('hot',1000,1000,rate=1000,qty=20)
    existing=[{'sku':'hot'} for _ in range(300)]
    rows=allocate_championship([c],{}, {'spendable':1000000},cfg,existing,{})
    assert len(rows)==1 and rows[0]['qty']==20
    assert sum(r['capital_commitment'] for r in rows)<=1000000
    assert not allocate_championship([c],{}, {'spendable':1000000},
        dict(cfg,shop_inventory_caps_enabled=True),existing,{})


def test_uncapped_exploration_prices_existing_and_additional_stock_risk():
    c=candidate('probe',1000,1000,rate=.1,qty=20,explore=True)
    c['evidence'].update(uncapped_inventory=True,sale_probability_scenario=.25)
    p=[marginal(c,n)['probability'] for n in range(1,10)]
    assert all(a>b for a,b in zip(p,p[1:]))
    assert marginal(c,1,held=20)['probability']<p[-1]
    rows=allocate_championship([c],{}, {'spendable':1000000},policy(),[],{})
    assert rows and 1<=rows[0]['qty']<20
    assert all(d['book']>0 for d in rows[0]['evidence']['marginal_units'])


def hot_inventory(j,now,qty=25):
    inv=j.snapshot('inventory.json');inv['units']=[{'unit':str(n),'sku':'G111','cost':500,
        'kind':'food','category':'food>one','expires_at':now+86400} for n in range(qty)]
    inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    atomic_json(j.md/'inventory.json',inv)
    j.put('merchant_context_v5',{'at':now,'skus':{'G111':{
        'arrival_rate_scenario':150,'paid_arrival_rate_scenario':150,
        'positive_paid_while_available':30,'available_hours_observed':1}},'coverage_weights':{}})
    return inv


def test_economy_replenishes_more_than_twenty_held_but_only_twenty_per_post(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=hot_inventory(j,now)
    rows=restock_candidates(inv,j.snapshot('catalog.json'),[],j,cfg,now)
    assert rows and 1<=rows[0]['qty']<=20 and rows[0]['sku']=='G111'
    assert rows[0]['evidence']['uncapped_inventory'] is True
    assert not restock_candidates(inv,j.snapshot('catalog.json'),[],j,
        dict(cfg,shop_inventory_caps_enabled=True),now)


def test_prepared_execution_does_not_reintroduce_held_cap_and_rechecks_new_stock(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=hot_inventory(j,now)
    packet=prewindow.prepare(j,cfg,{'version':1},now)
    assert packet and packet['options'][0]['buy']
    planned=prewindow.consume(j,cfg,{'version':1},inv,now)
    assert planned and planned['buy'] and planned['buy'][0]['qty']<=20
    # The same prepared plan cannot buy its original quantity again after it
    # has already been acquired; removing a held cap must not remove dedup.
    after=copy.deepcopy(inv);sku=planned['buy'][0]['sku']
    after['units'] += [dict(inv['units'][0],unit='new'+str(n)) for n in range(planned['buy'][0]['qty'])]
    repeated=prewindow.consume(j,cfg,{'version':1},after,now)
    assert repeated is not None and repeated['buy']==[]


def test_cash_and_storage_obligations_still_block_unaffordable_supply(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=hot_inventory(j,now)
    inv['balance']=1
    assert restock_candidates(inv,j.snapshot('catalog.json'),[],j,cfg,now)==[]


def test_background_research_can_distinguish_low_demand_targets_from_caps():
    inv={'units':[{'sku':'cold'},{'sku':'hot'}]}
    merchant={'at':time.time(),'skus':{'cold':{'arrival_rate_scenario':.05},
        'hot':{'positive_paid_while_available':20,'paid_arrival_rate_scenario':100}}}
    result=demand_diagnostics(inv,merchant,dict(policy(),shop_demand_horizon_cycles=3))
    assert result['inventory_unit_caps_enabled'] is False and result['total_unit_ceiling'] is None
    assert result['held_skus_target_at_most_two']==1 and result['held_skus_with_repeat_paid_evidence']==1
    assert result['target_shortfall_units_scenario']>20
    assert {r['rate_source'] for r in result['examples']}=={'own_positive_paid','public_prior_or_request_conversion'}
