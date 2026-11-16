from types import SimpleNamespace
from r3.departments import procurement_department,buyer_score_department
from r3.reversal import mean_refund

class Journal:
    def __init__(self, groups=None):
        self.data={'market_rules':{'sha256':'rules'},'public_feed_caught_up_at':999,
          'company-accounting:v30':{'at':999,'rules_hash':'rules','skus':groups or {}}}
    def get(self,key,default=None):return self.data.get(key,default)
    def put(self,key,value):self.data[key]=value
    def snapshot(self,key,default=None):return {}
    def events(self,kind,limit):return [{'_ts':999}]*20 if kind=='purchase' else []

def candidate(kind='food'):
    return {'sku':'s','qty':20,'capital_commitment':2000,'max_cost':2000,'score':10,
      'expected_delta':{'book':1},'evidence':{'kind':kind,'scenario_unit_cost':50,
       'resale_price_scenario':100,'planning_hours':1,'arrival_rate_scenario':1,
       'refund_stress':0,'refund_upper_scenario':0}}

def allocate(j,c,held=0):
    return procurement_department([c],{'units':[{'sku':'s'}]*held}, {'spendable':10000},
      {'restock_max_lines':10},j,{'s':{'kind':c['evidence']['kind']}},1000)

def test_food_full_unsold_cost_stops_depth():
    rows=allocate(Journal(),candidate())
    assert rows and rows[0]['qty']==1
    assert rows[0]['expected_delta']['book']>0

def test_existing_stock_exhausts_demand():
    assert allocate(Journal(),candidate(),held=10)==[]

def test_goods_requires_mature_net_profit():
    assert allocate(Journal(),candidate('goods'))==[]
    assert allocate(Journal({'s':{'mature_sales':5,'margin_after_refunds':-1}}),candidate('goods'))==[]

def test_money_weighted_refunds_can_veto_profitable_cohort():
    g={'mature_sales':5,'margin_after_refunds':1,'price':500,'refunds':400}
    assert allocate(Journal({'s':g}),candidate('goods'))==[]

def test_stale_feed_cannot_authorize_new_purchase():
    j=Journal();j.data['public_feed_caught_up_at']=0
    assert allocate(j,candidate())==[]

def test_buyer_target_adapts_to_distance_and_time():
    cfg={'market_end_at':'1970-01-01T01:16:40+00:00'}
    a=buyer_score_department(Journal(),{'saved':.75,'deals':100},[.76],cfg,1000)
    assert .752 in a and max(a)>.76

def test_seller_uses_upper_bound_in_finalday():
    r={'refund_mean':.01,'refund_upper':.2,'mean_validated':True,'mature_samples':50,'warranty_hours':6}
    assert mean_refund(r,Journal(),{'finalday_departments_enabled':True,'reversal_v28_enabled':True},1000)==.2

def test_accounting_links_money_and_reviews_without_double_counting(tmp_path):
    from r3.state import Journal as DB
    from r3.departments import accounting_department
    j=DB(tmp_path);j.put('market_rules',{'sha256':'r'})
    j.add('shop_settled',{'unit':'u','sku':'s','paid_at':100,'price':200,'cost':100},eid='settlement:9',ts=100)
    j.add('public_broken',{'unit':'u','refunded':200},ts=200)
    j.add('public_refund',{'unit':'u','refunded':200},ts=201)
    j.add('shop_review_observed',{'ledger':9,'author':'owner:a','stars':2,'source':'verified_market_result_and_accepted_own_task'},ts=202)
    assert accounting_department(j,{'s':{}},300)['skus']['s']['mature_sales']==0
    row=accounting_department(j,{'s':{}},23000)['skus']['s']
    assert row['mature_sales']==1 and row['margin_after_refunds']==-100
    assert row['refunds']==200 and row['reviews']==1 and row['low_reviews']==1

def test_llm_disabled_rejects_before_credentials_or_network():
    import pytest
    from r3.llm_roles import call_json
    from r3.model import ModelError
    with pytest.raises(ModelError,match='llm_disabled'):
        call_json(None,{'llm_disabled':True},'unused',{},'',{})
