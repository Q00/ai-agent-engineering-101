import copy
import math
import time
from r3.state import policy,atomic_json
from r3 import demand_policy,merchant,prewindow
from r3.shop_growth import target_stock
from test_market_cycle import fixture


def data(now):
    catalog={'G1':{'kind':'food','category':'fruit','shelf_hours':24,'list_price':1000},
             'G2':{'kind':'food','category':'fruit','shelf_hours':24,'list_price':1000},
             'G3':{'kind':'food','category':'fruit','shelf_hours':24,'list_price':1000}}
    public={s:{'external_samples':60,'samples':60,'sellers':1} for s in catalog}
    timeline=[{'at':now-60*i,'counts':{'G1':1,'G2':1,'G3':1},'reachable':True} for i in reversed(range(1,61))]
    sales=[{'unit':'u'+str(i),'sku':'G1','paid_at':now-20-i*100,'price':900,'cost':500} for i in range(12)]
    return catalog,public,timeline,sales


def fit(now,sales=None,timeline=None,supply=None):
    c,p,t,s=data(now)
    return demand_policy.fit(c,p,t if timeline is None else timeline,s if sales is None else sales,
        supply or [],{}, {'restock':{'opens_at':now+1200}},policy(),now)


def test_posterior_changes_with_observed_payments_instead_of_fixed_half():
    now=time.time();a=fit(now);b=fit(now,sales=[])
    assert a['summary']['calibration_multiplier_is_fixed'] is False
    assert a['skus']['G1']['rate_per_hour']>b['skus']['G1']['rate_per_hour']
    assert a['skus']['G1']['posterior_multiplier']!=.5
    assert all(math.isfinite(r['rate_per_hour']) and r['rate_per_hour']>=0 for r in a['skus'].values())


def test_cold_start_is_neutral_without_inventing_observed_demand():
    now=time.time();a=fit(now,sales=[],timeline=[])
    assert a['skus']['G1']['posterior_multiplier']==1
    assert a['skus']['G1']['weighted_paid']==0 and a['skus']['G1']['weighted_exposure_hours']==0


def test_known_offline_exposure_and_future_sales_do_not_train_rates():
    now=time.time();c,p,t,s=data(now)
    for row in t:row['reachable']=False
    a=demand_policy.fit(c,p,t,s,[],{}, {},policy(),now)
    assert a['skus']['G1']['weighted_paid']==0 and a['skus']['G1']['weighted_exposure_hours']==0
    x=fit(now);s.append({'unit':'future','sku':'G1','paid_at':now+5,'price':900,'cost':500})
    y=demand_policy.fit(c,p,data(now)[2],s,[],{}, {'restock':{'opens_at':now+1200}},policy(),now)
    assert x['skus']['G1']['rate_per_hour']==y['skus']['G1']['rate_per_hour']


def test_duplicate_units_are_not_double_paid_evidence():
    now=time.time();s=data(now)[3]
    a=fit(now,sales=s);b=fit(now,sales=s+s)
    assert a['skus']['G1']['weighted_paid']==b['skus']['G1']['weighted_paid']


def test_supply_failures_are_window_scoped_and_uncertainty_is_not_failure():
    now=time.time();w=now-1200
    events=[{'window':w,'sku':'G1','status':'retryable','_ts':w+10},
            {'window':w,'sku':'G1','status':'complete','_ts':w+20},
            {'window':w-1200,'sku':'G1','status':'uncertain','_ts':w-1100}]
    p,own,facts=demand_policy.supply_access(events,now)
    assert own['G1']==(1,0) and facts['failed_attempted_windows']==0
    assert p>.66


def test_measured_supply_risk_can_extend_coverage_beyond_old_hour_ceiling():
    now=time.time();cfg=policy();inv={'restock':{'opens_at':now+1200}}
    reliable=demand_policy.coverage_hours(inv,.95,cfg,now)
    unreliable=demand_policy.coverage_hours(inv,.25,cfg,now)
    assert reliable<1 and unreliable>1
    assert target_stock(8,10,cfg,coverage_hours=unreliable)>target_stock(8,10,cfg,coverage_hours=reliable)
    assert unreliable<=cfg['planning_horizon_hours']


def test_background_policy_expires_and_rebinds_to_rules(tmp_path):
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,shop_adaptive_demand_enabled=True)
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    j.put('merchant_stock_timeline',data(now)[2])
    packet=demand_policy.update(j,cfg,now)
    assert packet and demand_policy.context(j,cfg,now)['id']==packet['id']
    assert demand_policy.context(j,cfg,now+181) is None
    j.put('market_rules',{'sha256':'other'})
    assert demand_policy.context(j,cfg,now) is None


def test_merchant_and_prepared_plan_use_same_fresh_calibration_generation(tmp_path):
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,shop_adaptive_demand_enabled=True)
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    packet=demand_policy.update(j,cfg,now)
    m=merchant.context(j,j.snapshot('catalog.json'),now,config=cfg)
    assert m['demand_policy_id']==packet['id']
    row=m['skus']['G111'];assert row['arrival_rate_scenario']==packet['skus']['G111']['rate_per_hour']
    inv=j.snapshot('inventory.json');inv['restock']={'open':True,'opens_at':now-1,'closes_at':now+180}
    atomic_json(j.md/'inventory.json',inv)
    p=prewindow.prepare(j,cfg,{'version':1},now)
    assert p['demand_policy_id']==packet['id']
    changed=copy.deepcopy(packet);changed['id']='new-generation';j.put(demand_policy.KEY,changed)
    assert prewindow.consume(j,cfg,{'version':1},inv,now) is None
