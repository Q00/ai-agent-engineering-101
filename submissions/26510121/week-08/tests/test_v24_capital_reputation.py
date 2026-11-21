"""Financial boundaries and actual execution of optional-cap removal/quality policy."""
import copy
import time
import pytest
from r3.state import Journal,policy,atomic_json,identity
from r3 import reputation
from r3.economy import cash_capacity,shop_candidates
from r3.shop_growth import allocate_championship,target_stock,resale_terms
from r3.research import valid_operating
from r3.feedback import valid_patch
from test_shop_velocity import candidate
from test_market_contract import unit


def config(**kw):
    return dict(policy(),**dict(shop_internal_capital_caps_enabled=False,
        shop_reputation_v24_enabled=True,shop_inventory_caps_enabled=False,**kw))


def test_uncapped_cash_keeps_full_refunds_storage_and_release_schedule(tmp_path):
    j=Journal(tmp_path);now=time.time();inv={'balance':1000000,'units':[{'kind':'goods','cost':100000}]}
    for hours,age,uid in [(6,7,'mature'),(24,7,'long'),(72,7,'longer')]:
        j.add('shop_settled',{'unit':uid,'sku':'G1','price':100000,'paid_at':now-age*3600,
                            'warranty_hours':hours},ts=now-age*3600)
    old=cash_capacity(inv,j,dict(config(),shop_internal_capital_caps_enabled=True),now)
    new=cash_capacity(inv,j,config(),now)
    assert old['cash_floor']==750000 and new['cash_floor']==0
    assert new['refund_liability']==200000 and new['storage_to_end']>0
    assert new['spendable']==1000000-200000-new['storage_to_end']
    assert sum(r['amount'] for r in new['reserve_release_schedule'])==200000
    inv['balance']=199999
    assert cash_capacity(inv,j,config(),now)['spendable']==0


def test_single_category_can_use_capital_but_not_overdraw_or_exceed_legal_lots():
    rows=[candidate(str(n),10000,3000,rate=20,qty=20,category='same',explore=True) for n in range(12)]
    new=allocate_championship(rows,{}, {'spendable':500000},config(),[],{})
    used=sum(r['capital_commitment'] for r in new)
    assert 300000<used<=500000
    assert len(new)<=10 and all(0<r['qty']<=20 for r in new)
    assert sum(r['capital_commitment'] for r in new if r['evidence']['exploration'])>50000


def test_demand_control_exceeds_three_without_unbounded_forecast():
    cfg=config(shop_adaptive_demand_enabled=True,planning_horizon_hours=6)
    small=target_stock(10,8,dict(cfg,shop_demand_horizon_cycles=3),coverage_hours=.34)
    large=target_stock(10,8,dict(cfg,shop_demand_horizon_cycles=8),coverage_hours=.34)
    huge=target_stock(10,8,dict(cfg,shop_demand_horizon_cycles=1e50),coverage_hours=.34)
    boundary=target_stock(10,8,dict(cfg,shop_demand_horizon_cycles=18),coverage_hours=1/3)
    assert small<large<huge and huge==boundary
    assert valid_patch('shop',{'shop_demand_horizon_cycles':100})
    assert valid_operating({'cover_cycles':100,'initial_batch_limit':20,'frontier_min_orders':4,'recheck_seconds':60})
    assert not valid_patch('shop',{'shop_demand_horizon_cycles':float('inf')})


def test_quality_first_shipping_respects_buyer_requirement_and_reservations(tmp_path):
    from seller_r3 import Seller
    j=Journal(tmp_path);seller=Seller(j);seller.cfg=config()
    a=unit(uid='new-a');c=unit(uid='old-c');c.update(grade='C',supplied_at=time.time()-1000)
    atomic_json(j.md/'inventory.json',{'round':3,'units':[c,a]})
    atomic_json(j.md/'catalog.json',{'G111':{'kind':'goods'}})
    assert seller.select_unit('G111','one')['unit']=='new-a'
    assert seller.select_unit('G111 require grade C','two')['unit']=='old-c'
    assert seller.select_unit('G111 require grade A','three') is None


@pytest.mark.parametrize('promise',[None,0,3,6,24,72])
def test_new_full_horizon_warranty_and_existing_promises(tmp_path,promise):
    j=Journal(tmp_path);u=unit()
    if promise is not None:u['promised_warranty_hours']=promise
    sales=[c for c in shop_candidates(u,{'balance':5000000},{},j,config()) if c['action']!='hold']
    choices={c['warranty_hours'] for c in sales}
    assert choices==({6,24,72} if promise is None else {promise})
    assert all(c['price']>=c['recovery_floor'] for c in sales)


def test_procurement_cannot_price_new_insured_goods_as_old_uninsured(tmp_path):
    j=Journal(tmp_path);now=time.time()
    public={'median':8000,'term_prices':[{'grade':g,'warranty_hours':0,'median':9000,'n':20} for g in 'ABC']}
    for g in 'ABC':
        for n in range(30):
            j.add('shop_settled',{'unit':g+str(n),'sku':'G1','grade':g,'paid_at':now-25000},ts=now-25000)
    atomic_json(j.md/'catalog.json',{'G1':{'kind':'goods'}})
    r=resale_terms({'kind':'goods','list_price':10000},public,j,config(),now)
    assert r['refund_stress']>0 and all(t['warranty_hours']>=6 for t in r['terms'])
    assert all(t['observations']==0 for t in r['terms'])


def test_account_mean_innovation_uses_past_only_and_excludes_left_censored_start():
    rows=[{'author':'a','stars':v} for v in [5,5,5,3,5]]
    result=reputation.innovations(rows)
    assert len(result)==2 and result[0]['innovation']==-.5
    assert result[0]['prior_account_mean']==5 and result[0]['prior_account_n']==3
    assert result[1]['innovation']==.1
    assert reputation.innovations([{'author':'a','stars':5}]*10)[-1]['innovation']==0


def test_shadow_model_cannot_grant_rating_bonus_or_ignore_packet_drift(tmp_path):
    j=Journal(tmp_path);now=time.time();j.put('market_rules',{'sha256':'r'});j.put('board_checked_at',now)
    atomic_json(j.md/'board.json',{'shops':[{'name':'seochanit','stars_rank':4.3,'reviews':100}]})
    p={'at':now,'expires_at':now+180,'rules_hash':'r','model':{'fields':['grade'],'groups':{'A':{'mean':.2}}},
       'summary':{'mode':'shadow'},'downside':{},'owner_counts_observed':{'a':100},
       'evidence_reviews':90,'raw_reviews':100,'observed_accounts':1}
    p['id']=identity(p);j.put(reputation.KEY,p)
    options=[{'id':'accept','action':'accept','price':8000,'warranty_hours':6,'expected_delta':{'book':2000},'evidence':{}}]
    assert 'stars_rank' not in reputation.augment(j,config(),unit(),options,now)[0]['expected_delta']
    p['summary']['mode']='validated_predictive_surrogate';j.put(reputation.KEY,p)
    assert reputation.packet(j,now)=={}  # Checksum mismatch cannot activate a shadow model.


def test_supported_downside_applies_to_accept_and_counter_without_mutating_money(tmp_path):
    j=Journal(tmp_path);now=time.time();j.put('market_rules',{'sha256':'r'});j.put('board_checked_at',now)
    atomic_json(j.md/'board.json',{'shops':[{'name':'seochanit','stars_rank':4.3,'reviews':100}]})
    p={'at':now,'expires_at':now+180,'rules_hash':'r','model':{'fields':['grade'],'groups':{}},
       'summary':{'mode':'shadow'},'downside':{'goods|C|True':{'reviews':40,'owners':8,'upper90':-.03}},
       'owner_counts_observed':{'a':100},'evidence_reviews':90,'raw_reviews':100,'observed_accounts':8}
    p['id']=identity(p);j.put(reputation.KEY,p)
    rows=[{'id':a,'action':a,'price':8000,'warranty_hours':6,'recovery_floor':7000,
           'expected_delta':{'book':1000},'evidence':{}} for a in ('accept','counter')]
    before=copy.deepcopy(rows);u=unit();u['grade']='C'
    reputation.augment(j,config(),u,rows,now)
    assert all(r['expected_delta']['stars_rank']<0 for r in rows)
    assert all(r['expected_delta']['book']==b['expected_delta']['book'] and r['price']==b['price'] and
               r['recovery_floor']==b['recovery_floor'] for r,b in zip(rows,before))
    assert reputation.packet(j,now+181)=={}


def test_constant_ratings_do_not_pass_predictive_gate():
    raw=[{'author':str(n%12),'stars':5,'grade':'A','kind':'goods','insured':True,'cheap':False} for n in range(600)]
    _,s=reputation.select(reputation.innovations(raw))
    assert s['mode']=='shadow' and s['selection_uses_tune_only'] and s['zero_change_test_mse']==0
