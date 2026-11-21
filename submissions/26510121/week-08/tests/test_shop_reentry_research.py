import pytest
from r3.shop_reentry_research import eligible,compare


def row():
    return dict(sku='s',kind='goods',mature_sales=1,mature_margin=200,low_reviews=0,
        forecast_book_without_sku_gate=100,forecast_revenue_without_sku_gate=500,
        capital=1000,candidate_qty=2,positive_marginal_qty=1,external_samples=6,
        latest_external_observation=999,external_buyers=[['a',3],['b',3]])


def test_profitable_mature_warm_sku_with_diverse_external_demand_is_eligible():
    assert eligible(row(),1000)


@pytest.mark.parametrize('key,value',[('mature_sales',0),('mature_sales',3),('mature_margin',-1),
    ('low_reviews',1),('external_samples',5),('latest_external_observation',-1000),
    ('latest_external_observation',1001),('forecast_book_without_sku_gate',-1),
    ('forecast_book_without_sku_gate',float('nan')),('positive_marginal_qty',21)])
def test_missing_safety_or_profit_evidence_rejects_reentry(key,value):
    r=row();r[key]=value
    assert not eligible(r,1000)


def test_duplicate_buyer_names_do_not_manufacture_diverse_demand():
    r=row();r['external_buyers']=[['a',3],['a',3]]
    assert not eligible(r,1000)


def test_shadow_allocation_respects_total_capital_and_never_authorizes_execution():
    r=row();audit={'at':1000,'candidates':[r],'baseline_buy':[],
        'financial_capacity':{'spendable':499}}
    assert compare(audit)['proposed']==[]
    audit['financial_capacity']['spendable']=500
    p=compare(audit)
    assert p['proposed'][0]['qty']==1 and p['proposed_capital']==500
    assert p['execution_allowed'] is False


def test_expensive_warm_sku_cannot_displace_a_better_existing_portfolio():
    warm=row()
    foods=[dict(row(),sku=s,kind='food',capital=200,candidate_qty=1) for s in ('a','b')]
    audit={'at':1000,'candidates':[warm,*foods],
        'baseline_buy':[{'sku':s,'qty':1,'book':60} for s in ('a','b')],
        'financial_capacity':{'spendable':500}}
    result=compare(audit)
    assert result['proposed_book']==120
    assert {r['sku'] for r in result['proposed']}=={'a','b'}
