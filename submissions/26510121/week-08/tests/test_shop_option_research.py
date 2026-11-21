import pytest
from r3.shop_option_research import marginal_a_sale,option_rows


def data():
    return ({'s':{'samples':24,'buyers':3,'age_seconds':1,'price_p25':950,'competitors':1,'surge_factor':1}},
        {'s':{'kind':'goods','list_price':1000}}, {}, {'refund_upper':.1})


def test_first_draw_counts_only_the_published_a_probability():
    from math import exp
    assert marginal_a_sale(1,2)==pytest.approx(.7*(1-exp(-2)))
    assert marginal_a_sale(1,0)==0


def test_depth_and_existing_eligible_stock_reduce_sale_probability():
    assert marginal_a_sale(1,2)>marginal_a_sale(2,2)>marginal_a_sale(5,2)
    assert marginal_a_sale(1,2,0)>marginal_a_sale(1,2,1)


def test_terminal_unsold_draws_retain_book_but_pay_storage():
    rows=option_rows(*data(),6,.5)
    assert rows and rows[0]['forecast_book']>0
    assert rows[0]['capital']>=rows[0]['qty']*1125
    assert rows[0]['forecast_book']==pytest.approx(sum(rows[0]['marginal_book']))


@pytest.mark.parametrize('field,value',[('samples',2),('buyers',1),('age_seconds',1801),('price_p25',700),('surge_factor',1.5)])
def test_inadequate_contract_support_or_negative_margin_has_no_option(field,value):
    observed,catalog,held,risk=data();observed['s'][field]=value
    assert option_rows(observed,catalog,held,risk,6,.5)==[]


def test_mean_failure_cannot_be_used_without_temporal_validation():
    args=data()
    assert option_rows(*args,6,.5,use_mean=True)==[]
    risk=dict(args[-1],refund_mean=.02,mean_validated=True,mature_samples=40)
    assert option_rows(*args[:3],risk,6,.5,use_mean=True)


def test_lower_demand_never_raises_forecast_book():
    assert option_rows(*data(),6,.5,rate_scale=.5)[0]['forecast_book']<=option_rows(*data(),6,.5)[0]['forecast_book']
