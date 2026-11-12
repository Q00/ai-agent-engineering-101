"""Patch 3.5 raw forum scoring and paid-power rule detection, with no votes."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from r3.state import Journal,policy
from r3.meta import rank_gain,rank_progress,rank_targets
from r3.control import audit_rules


def board(peers=2):
    return {'round':3,'buyer_metric':'saved','buyer_forum_metric':'raw','buyers':[
        {'name':'seochanit','saved':.3,'deals':100,'posts':10,'forum':.89}]+[
        {'name':str(n),'saved':.3,'deals':100,'posts':10,'forum':.8901+n*.00001} for n in range(peers)]}


def test_forum_one_percentage_point_is_point003_not_a_percentile_jump():
    for peers in [0,2,100]:
        b=board(peers)
        assert rank_gain(b,'buyer','seochanit',{'forum':.01})==pytest.approx(.003)
        assert rank_progress(b,'buyer','seochanit',{'forum':.01})==pytest.approx(.003)


def test_direct_forum_is_clipped_to_the_official_zero_one_range():
    assert rank_gain(board(),'buyer','seochanit',{'forum':1})==pytest.approx(.3*.11)
    assert rank_gain(board(),'buyer','seochanit',{'forum':-1})==pytest.approx(-.3*.89)


def test_forum_targets_are_benchmarks_while_saved_stays_percentile():
    targets=rank_targets(board(),'buyer')
    assert targets['forum']['scale']=='raw' and targets['forum']['all_gain_per_0_01']==.003
    assert targets['saved']['scale']=='percentile'


def rule_text():
    return ('shops by book, revenue and stars; each further unit adds 0.2%; up to +50%; '
            'buyers by saved rate; ALL weighs them 0.5, 0.3, 0.1, 0.1; divided by deals + 100; '
            'surplus / list price; counts -1, or 0 if refunded; except the forum rating, which counts as itself (0 to 1). '
            'A dislike with value -N (N up to 1000); unit x (N - 1)^2; 3% of largest Book; '
            'power_fee and next_vote_fee; at least 50 settled deals')


def test_patch35_audit_records_direct_rating_and_paid_power(tmp_path):
    j=Journal(tmp_path);result=audit_rules(j,rule_text(),1000)
    assert result['buyer_compatible'] and result['buyer_forum_metric']=='raw'
    assert result['paid_dislike_power'] and result['paid_dislike_contract_known']
    assert j.events('market_scoring_definition')


def test_unknown_forum_definition_does_not_pass_the_old_weight_only_audit(tmp_path):
    j=Journal(tmp_path)
    changed=rule_text().replace('which counts as itself (0 to 1)','which uses a new rule')
    result=audit_rules(j,changed,1000)
    assert result['buyer_forum_metric']=='unknown' and not result['buyer_compatible']


def test_unknown_paid_power_contract_is_not_claimed_as_understood(tmp_path):
    j=Journal(tmp_path)
    result=audit_rules(j,rule_text().replace('(N - 1)^2','(N - 1)^3'),1000)
    assert result['paid_dislike_power'] and not result['paid_dislike_contract_known']


def test_food_price_projection_uses_food_grade_mix_not_goods_mix(tmp_path):
    from r3.shop_growth import resale_terms
    j=Journal(tmp_path)
    observed={'median':700,'term_prices':[{'grade':g,'warranty_hours':0,'median':p,'n':10}
                                        for g,p in [('A',900),('B',700),('C',400)]]}
    result=resale_terms({'kind':'food','list_price':1000},observed,j,
                        dict(policy(),shop_championship_enabled=True),1000)
    assert result['price']==675


def test_direct_forum_is_not_double_counted_by_exact_plus_smooth_choice(tmp_path):
    from r3.state import atomic_json
    from r3.model import Chooser
    j=Journal(tmp_path)
    atomic_json(j.md/'board.json',board())
    j.put('market_rules',{'buyer_metric':'saved','buyer_forum_metric':'raw'})
    candidates=[{'id':'better','score':0,'expected_delta':{'forum':.01}},
                {'id':'same','score':0,'expected_delta':{'forum':0}}]
    chooser=Chooser(j,dict(policy(),buyer_patch34_enabled=True))
    assert chooser.choose('buyer',candidates,{'buyer_execution':True})['id']=='better'
    assert candidates[0]['objective_utility']==pytest.approx(.003)
