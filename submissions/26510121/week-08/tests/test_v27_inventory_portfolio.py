"""Demand-supported depth, whole-lot competition, downside and financial invariants."""
import copy
import json
import time
import pytest
from r3.state import policy,Journal,atomic_json
from r3.inventory_portfolio import allocate,supported,dominates,context,observe
from test_market_cycle import fixture


def candidate(sku='G111',rate=3,cost=590,price=950,qty=20,baseline=2,kind='goods',supported=True):
    return {'sku':sku,'id':sku,'qty':qty,'capital_commitment':1125*qty,'max_cost':1125*qty,
        'category':sku,'score':qty*(price-cost),'rank_progress_surrogate':None,
        'expected_delta':{'book':qty*(price-cost),'profit':qty*price,'revenue':qty*price},
        'evidence':{'kind':kind,'list_price':1000,'growth':{'positive_paid_while_available':20},
            'arrival_rate_scenario':rate,'planning_hours':6,'scenario_unit_cost':cost,
            'resale_price_scenario':price,'refund_stress':.02 if kind=='goods' else 0,
            'exploration':False,'expected_holding_hours':1,'adaptive_category_weight':1,
            'supply_surge_factor':1,'v27_baseline_qty':baseline,'v27_depth_supported':supported}}


def run(cs,cash=10000000,held=None,**patch):
    audit={};cfg=dict(policy(),finalday_departments_enabled=False,llm_disabled=False,restock_max_lines=2);cfg.update(patch)
    rows=allocate(cs,{}, {'spendable':cash},cfg,held or [],{},audit=audit)
    return rows,audit


def test_supported_depth_needs_real_payments_or_diverse_fresh_public_activity():
    now=time.time();pub={'external_samples':20,'external_buyers':[('a',10),('b',5),('c',5)],'latest_at':now-1}
    assert supported({},pub,now) and supported({'positive_paid_while_available':3},{},now)
    assert not supported({},dict(pub,external_samples=19),now)
    assert not supported({},dict(pub,external_buyers=[('a',20)]),now)
    assert not supported({},dict(pub,latest_at=now-3601),now)
    assert not supported({},dict(pub,external_buyers=[('a',15),('seochanit',3),('unknown',2)]),now)


def test_strategic_depth_increases_only_with_better_shared_economics():
    cs=[candidate()];before=copy.deepcopy(cs);rows,audit=run(cs)
    assert cs==before and rows[0]['qty']>2
    assert audit['dominance_guard_passed'] and dominates(audit['alternatives'][audit['selection']],audit['alternatives']['baseline'])
    assert rows[0]['expected_delta']['book']>audit['alternatives']['baseline']['book']
    assert len(json.dumps(rows[0]['evidence']['inventory_portfolio_v27']))<250
    assert '_inventory_portfolio_packet' not in rows[0]


def test_more_than_twenty_held_is_allowed_but_one_post_stays_legal():
    rows,audit=run([candidate(rate=30,baseline=0)],held=[{'sku':'G111'} for _ in range(60)])
    assert rows and rows[0]['qty']==20
    assert audit['selected'][0]['held']==60


def test_high_existing_inventory_prevents_repeated_demand_double_counting():
    rows,audit=run([candidate(rate=1,baseline=0)],held=[{'sku':'G111'} for _ in range(100)])
    assert rows==[] and audit['alternatives'][audit['selection']]['book']==0


@pytest.mark.parametrize('cash',[0,1,1124,1125,2500,10000])
def test_lots_never_bypass_worst_case_cash_or_line_bounds(cash):
    rows,audit=run([candidate('G111',baseline=0),candidate('G222',baseline=0)],cash=cash)
    assert sum(r['capital_commitment'] for r in rows)<=cash+1e-6
    assert len(rows)<=2 and all(1<=r['qty']<=20 for r in rows)


def test_unsupported_new_demand_cannot_receive_a_large_strategic_lot():
    rows,audit=run([candidate(baseline=0,supported=False)])
    assert rows==[]


def test_empty_and_zero_rate_portfolios_return_auditable_wait():
    rows,audit=run([]);assert rows==[] and audit['dominance_guard_passed']
    rows,audit=run([candidate(rate=0,baseline=0)])
    assert rows==[] and audit['alternatives']['baseline']['capital']==0


def test_surge_can_remove_a_supported_opportunity():
    c=candidate(cost=885,price=850,baseline=0);c['evidence']['supply_surge_factor']=1.5
    rows,audit=run([c]);assert rows==[]


def test_whole_legal_lot_value_can_beat_a_single_high_roi_item():
    cheap=candidate('G111',cost=590,price=950,qty=1,baseline=0)
    valuable=candidate('G222',cost=590,price=900,qty=20,baseline=0)
    rows,audit=run([cheap,valuable],restock_max_lines=1)
    assert rows[0]['sku']=='G222' and rows[0]['qty']>1


def test_downside_and_mean_guard_are_independent():
    b={'book':10,'stress_book':2,'rank_progress':.01}
    assert not dominates(dict(b,book=9),b)
    assert not dominates(dict(b,stress_book=1),b)
    assert not dominates(dict(b,rank_progress=.009),b)
    assert dominates(dict(b,book=11,stress_book=3),b)


def test_every_selected_proposal_retains_the_old_portfolio_benchmark():
    cs=[candidate('G111',rate=3,kind='food'),candidate('G222',rate=2,price=800),candidate('G333',rate=.5,price=720)]
    rows,audit=run(cs)
    selected=audit['alternatives'][audit['selection']];old=audit['alternatives']['baseline']
    assert selected['book']>=old['book']-1e-6 and selected['stress_book']>=old['stress_book']-1e-6
    assert len(audit['alternatives'])>=7


def test_expiring_packet_or_changed_rules_cannot_enter_llm_context(tmp_path):
    j=Journal(tmp_path);now=time.time();j.put('market_rules',{'sha256':'one'})
    j.put('inventory-portfolio:v27',{'at':now,'expires_at':now+180,'rules_hash':'one','selection':'baseline'})
    assert context(j,now) and not context(j,now+181)
    j.put('market_rules',{'sha256':'two'});assert not context(j,now)


def test_observer_is_numeric_and_does_not_trade_or_call_models(tmp_path):
    j=Journal(tmp_path);cfg=policy();row=observe(j,cfg)
    assert row and j.get('inventory-observer:v27')==row
    assert not j.events('model_usage') and not j.events('purchase') and not j.events('shop_settled')


def test_seller_risk_is_cached_only_within_a_single_forecast(tmp_path,monkeypatch):
    from r3.shop_growth import resale_terms
    from r3.decision_view import frame
    j=Journal(tmp_path);calls=[]
    monkeypatch.setattr('r3.risk.shop_refund_risk',lambda *a: calls.append(1) or {'refund_upper':.1})
    for _ in range(2):
        with frame(j):
            for n in range(10):resale_terms({'kind':'goods','list_price':1000},{},j,policy(),time.time())
    assert len(calls)==6


def test_economy_expands_supported_depth_without_changing_the_baseline_definition(tmp_path):
    from r3.economy import restock_candidates
    j,cfg,now=fixture(tmp_path);inv=j.snapshot('inventory.json');inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    j.put('merchant_context_v5',{'at':now,'demand_policy_id':None,'service_policy_at':None,'skus':{
        'G111':{'arrival_rate_scenario':3,'paid_arrival_rate_scenario':3,'positive_paid_while_available':20}},'coverage_weights':{}})
    old=restock_candidates(inv,j.snapshot('catalog.json'),[],j,cfg,now)
    rows=restock_candidates(inv,j.snapshot('catalog.json'),[],j,dict(cfg,inventory_portfolio_v27_enabled=True),now)
    audit=j.get('inventory-portfolio:v27');b=audit['alternatives']['baseline']
    assert b['units']==sum(r['qty'] for r in old) and b['book']==pytest.approx(sum(r['expected_delta']['book'] for r in old))
    assert sum(r['qty'] for r in rows)>sum(r['qty'] for r in old)


def test_cost_routing_and_buyer_policy_remain_unchanged():
    cfg=policy();expected=('buyer-v30-adaptive-bid-frontier' if cfg.get('finalday_departments_enabled') else 'buyer-v28-all-progress-per-second' if cfg.get('reversal_v28_enabled') else 'buyer-v26-exact-contract-continuation')
    assert cfg['inventory_portfolio_v27_enabled'] and cfg['buyer_strategy_revision']==expected
    assert cfg['daily_api_budget_usd'] is None and not cfg['shop_inventory_caps_enabled'] and not cfg['shop_internal_capital_caps_enabled']
    assert cfg['background_llm_roles']['market_research_analyst']['model']=='gpt-6-luna'
    assert cfg['background_llm_roles']['market_research_critic']['model']=='gpt-6.1-terra'
    assert all('astra' not in r['model'] for r in cfg['background_llm_roles'].values())


def test_strategic_portfolio_is_prepared_and_consumed_without_an_extra_model_call(tmp_path,monkeypatch):
    from r3.prewindow import prepare,consume
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,inventory_portfolio_v27_enabled=True)
    monkeypatch.setenv('R3_MODEL_MODE','offline')
    plan=prepare(j,cfg,{'version':1},now);assert plan
    inv=j.snapshot('inventory.json');inv['restock']['open']=True
    result=consume(j,cfg,{'version':1},inv,now+61)
    assert result is not None and j.events('shop_prepared_plan_used')
    assert j.get('inventory-portfolio:v27')['dominance_guard_passed']
    assert not j.events('model_usage')
