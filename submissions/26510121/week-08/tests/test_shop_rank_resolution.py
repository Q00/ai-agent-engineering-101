import time
import pytest
from r3.state import Journal,atomic_json,policy
from r3.model import Chooser,threshold_delta
from r3.meta import rank_gain


def fixture(tmp_path):
    j=Journal(tmp_path)
    board={'round':3,'shops':[
        dict(name='seochanit',deals=100,book=1000000,revenue=10000000,stars_rank=4.30),
        dict(name='a',deals=100,book=2000000,revenue=11000000,stars_rank=4.30),
        dict(name='b',deals=100,book=500000,revenue=5000000,stars_rank=4.29),
        dict(name='c',deals=100,book=3000000,revenue=12000000,stars_rank=4.40)]}
    atomic_json(j.md/'board.json',board);j.put('board_checked_at',time.time())
    cfg=dict(policy(),objective_feedback_enabled=False,system_framework_enabled=False,
        autonomy_v14_enabled=False,market_cycle_enabled=False,llm_disabled=True)
    return j,board,cfg


def options(stars=-.0000006):
    return [dict(id='quote',action='counter',price=11000,score=1000,
        expected_delta=dict(book=1000,revenue=10000,stars_rank=stars),
        evidence={'account_reputation':{'official_mapping_exact':False}}),
        dict(id='hold',action='hold',price=0,score=0,expected_delta=dict(book=0,revenue=0))]


def test_unverified_rating_epsilon_cannot_veto_a_profitable_quote_at_a_published_tie(tmp_path):
    j,board,cfg=fixture(tmp_path);candidates=options()
    assert rank_gain(board,'shop','seochanit',candidates[0]['expected_delta'])<-.01
    selected=Chooser(j,cfg).choose('shop',candidates,{'shop_execution':True,'operation':'quote'})
    assert selected['id']=='quote'
    assert selected['rank_gain_approximation']==0
    assert selected['rank_progress_surrogate']>0
    assert selected['expected_delta']['stars_rank']<0


def test_material_expected_reputation_damage_still_can_favor_holding(tmp_path):
    j,_,cfg=fixture(tmp_path)
    assert Chooser(j,cfg).choose('shop',options(-.05),{'shop_execution':True})['id']=='hold'


def test_verified_official_mapping_keeps_exact_rank_effect(tmp_path):
    j,_,cfg=fixture(tmp_path);candidates=options()
    candidates[0]['evidence']['account_reputation']['official_mapping_exact']=True
    assert Chooser(j,cfg).choose('shop',candidates,{'shop_execution':True})['id']=='hold'


def test_unverified_positive_delta_cannot_invent_an_exact_rank_bonus(tmp_path):
    j,_,cfg=fixture(tmp_path);candidates=options(.0000006)
    selected=Chooser(j,cfg).choose('shop',candidates,{'shop_execution':True})
    assert selected['rank_gain_approximation']==0
    assert 0<selected['rank_progress_surrogate']<.01


@pytest.mark.parametrize('evidence',[{},'not a mapping',{'account_reputation':{'official_mapping_exact':'true'}}])
def test_missing_or_non_boolean_mapping_proof_is_not_accepted(evidence):
    delta={'book':1,'stars_rank':-.000001}
    assert threshold_delta('shop',{'evidence':evidence},delta)=={'book':1}
    assert delta['stars_rank']==-.000001


def test_buyer_scoring_is_unchanged():
    delta={'saved':.001,'stars_rank':.1}
    assert threshold_delta('buyer',{},delta) is delta
