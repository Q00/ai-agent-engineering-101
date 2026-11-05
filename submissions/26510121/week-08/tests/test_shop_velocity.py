import math
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from r3.state import Journal,policy,atomic_json
from r3.shop_growth import poisson_tail,target_stock,resale_terms,marginal,allocate_velocity
from r3.adaptive import activate_revision,activate_shop_revision
from r3.model import Chooser


def config(**kwargs):
    return dict(policy(),**dict(shop_velocity_enabled=True,shop_fast_execution_enabled=True,
        restock_max_qty=12,**kwargs))


def test_paid_demand_can_batch_beyond_six_but_unknown_demand_cannot():
    cfg=config()
    assert 6<target_stock(25,4,cfg)<=12
    assert target_stock(25,0,cfg)<=2
    assert target_stock(0,4,cfg)==1
    assert poisson_tail(0,1)==0
    assert abs(poisson_tail(1,1)-(1-math.exp(-1)))<1e-12
    assert poisson_tail(1,4)<poisson_tail(1,2)<poisson_tail(1,1)


def test_uninsured_observed_sales_do_not_have_fictitious_refund_cost(tmp_path):
    j=Journal(tmp_path);public={'median':7000,'term_prices':[
        {'grade':g,'warranty_hours':0,'median':8000,'n':10} for g in ['A','B','C']]}
    result=resale_terms({'kind':'goods','list_price':10000},public,j,config(),time.time())
    assert abs(result['price']-8000)<1e-8 and result['refund_stress']==0
    assert all(r['warranty_hours']==0 for r in result['terms'])


def test_warranty_refund_stress_cannot_be_hidden_by_term_matching(tmp_path):
    j=Journal(tmp_path);public={'median':7000,'term_prices':[
        {'grade':g,'warranty_hours':6,'median':9000,'n':10} for g in ['A','B','C']]}
    result=resale_terms({'kind':'goods','list_price':10000},public,j,config(),time.time())
    assert result['refund_stress']>=config()['unknown_goods_failure_stress']-1e-10
    assert all(r['warranty_hours']==6 for r in result['terms'])


def candidate(sku,cost,margin,rate=3,qty=4,category=None,explore=False):
    return {'sku':sku,'category':category or sku,'qty':qty,'capital_commitment':cost*qty,
        'max_cost':cost*qty,'expected_delta':{'book':margin*qty,'revenue':(cost+margin)*qty},
        'score':margin*qty,'rank_progress_surrogate':margin*qty/1000000,
        'evidence':{'kind':'goods','exploration':explore,'planning_hours':6,
            'arrival_rate_scenario':rate,'resale_price_scenario':cost+margin,
            'scenario_unit_cost':cost,'refund_stress':0,'sale_probability_scenario':.8,
            'expected_holding_hours':1/rate,'growth':{'coverage_keys':[]}}}


def test_deeper_inventory_has_diminishing_sell_through():
    c=candidate('slow',1000,1000,rate=.1,qty=12)
    first,last=marginal(c,1),marginal(c,12)
    assert last['probability']<first['probability']
    assert last['book']<first['book'] and last['book']<0


def test_capital_velocity_prefers_fast_profitable_repeat_inventory():
    fast=candidate('fast',200000,40000,rate=10,qty=6)
    slow=candidate('slow',1200000,150000,rate=.3,qty=1)
    rows=allocate_velocity([slow,fast],{}, {'spendable':1500000},config(),[],{})
    assert any(r['sku']=='fast' and r['qty']>=3 for r in rows)
    assert not any(r['sku']=='slow' for r in rows)
    assert sum(r['capital_commitment'] for r in rows)<=1500000


def test_allocation_caps_cash_category_lines_and_exploration():
    rows=[candidate(str(n),10000,3000,rate=5,qty=12,category='same' if n<5 else str(n),
                    explore=n>=8) for n in range(12)]
    cfg=config(restock_max_lines=4)
    selected=allocate_velocity(rows,{}, {'spendable':300000},cfg,[],{})
    assert len(selected)<=4 and sum(r['capital_commitment'] for r in selected)<=300000
    assert sum(r['capital_commitment'] for r in selected if r['category']=='same')<=180000
    assert sum(r['capital_commitment'] for r in selected if r['evidence']['exploration'])<=30000


def test_fast_shop_decision_keeps_prediction_without_model_request(tmp_path,monkeypatch):
    monkeypatch.delenv('R3_MODEL_MODE',raising=False)
    j=Journal(tmp_path);chooser=Chooser(j,config())
    def never(*args,**kwargs):raise AssertionError('unnecessary model call')
    monkeypatch.setattr(chooser,'_call',never)
    chosen=chooser.choose('shop',[{'id':'positive','score':100,'expected_delta':{'book':100}},
        {'id':'hold','score':0,'expected_delta':{'book':0}}],{'shop_execution':True})
    assert chosen['id']=='positive' and j.events('prediction')
    assert j.events('decision')[0]['mode']=='fast_shop_numeric'


def test_large_quote_still_gets_critical_review(tmp_path,monkeypatch):
    monkeypatch.delenv('R3_MODEL_MODE',raising=False)
    j=Journal(tmp_path);chooser=Chooser(j,config());calls=[]
    candidates=[{'id':'good','score':100},{'id':'bad','score':0}]
    def call(role,*a,**kw):calls.append(role);return candidates[0]
    monkeypatch.setattr(chooser,'_call',call)
    chooser.choose('shop',candidates,{'shop_execution':True},critical=True)
    assert calls==['shop_actor','shop_critic']


def test_shop_release_does_not_reset_buyer_evidence_clock(tmp_path):
    j=Journal(tmp_path);now=time.time()
    activate_revision(j,'old',now-3000)
    buyer=j.get('adaptive:buyer');epoch=j.get('objective_epoch')
    activate_shop_revision(j,'velocity-v7',now)
    assert j.get('adaptive:buyer')==buyer and j.get('objective_epoch')==epoch
    assert j.get('shop_revision_epoch')==now
    assert j.get('adaptive:shop')['version']==buyer['version']+1
    version=j.get('adaptive:shop')['version']
    activate_shop_revision(j,'velocity-v7',now+1)
    assert j.get('adaptive:shop')['version']==version


def test_historic_losses_cannot_raise_margin_after_a_clean_shop_release(tmp_path):
    from r3.control import update,audit_rules
    j=Journal(tmp_path);now=time.time();j.put('objective_epoch',now-1000)
    j.put('shop_revision_epoch',now-100)
    for n in range(8):
        j.add('shop_settled',{'sku':'G1','price':1,'cost':10},ts=now-200-n)
        j.add('shop_settled',{'sku':'G1','price':20,'cost':10},ts=now-50-n)
    atomic_json(j.md/'board.json',{'round':3,'shops':[{'name':'seochanit','book':100,'all':.5}],
                                 'buyers':[{'name':'seochanit','all':.5}],'players':[]})
    atomic_json(j.md/'inventory.json',{'round':3,'balance':5000000,'units':[]})
    atomic_json(j.md/'catalog.json',{})
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    audit_rules(j,'shops by book, revenue and stars; each further unit adds 0.2%, up to +50%',now)
    result=update(j,now=now,force=True)
    assert result['below_cost_sales']==8 and result['current_shop_below_cost']==0
    assert not any(s['field']=='shop_margin_fraction' for s in result['suggestions'])


def test_shop_revision_can_initialize_an_empty_journal(tmp_path):
    j=Journal(tmp_path)
    activate_shop_revision(j,'first',100)
    assert j.get('adaptive:shop')['version']==1
    assert j.get('shop_strategy_revision')=='first'
    assert j.get('adaptive:buyer') is None


def test_buyer_offering_above_our_ask_gets_a_receipt_without_an_extra_counter(tmp_path):
    from r3.economy import shop_candidates
    j=Journal(tmp_path);now=time.time()
    unit={'sku':'G1','kind':'goods','grade':'A','list_price':10000,'cost':5000,'supplied_at':now,
          'promised_warranty_hours':0}
    candidates=shop_candidates(unit,{'balance':5000000},{'G1':{}},j,config(),offer=8500,previous_ask=8000,now=now)
    accepts=[c for c in candidates if c['action']=='accept']
    assert accepts and all(c['price']==8000 for c in accepts)


def test_price_response_prior_does_not_assume_higher_prices_convert_equally(tmp_path):
    from r3.economy import shop_candidates
    j=Journal(tmp_path);now=time.time()
    unit={'sku':'G1','kind':'food','grade':'A','list_price':10000,'cost':5000,'supplied_at':now,
          'expires_at':now+86400}
    quotes=[c for c in shop_candidates(unit,{'balance':5000000},{'G1':{'shelf_hours':48}},j,config(),now=now)
            if c['action']=='counter']
    low,high=min(quotes,key=lambda c:c['price']),max(quotes,key=lambda c:c['price'])
    assert low['evidence']['quote_completion']['completion']>high['evidence']['quote_completion']['completion']
