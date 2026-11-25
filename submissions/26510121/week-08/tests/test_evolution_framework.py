import time
from r3.state import Journal,atomic_json,policy
from r3.evolution import update,assign,evaluate
from r3.adaptive import propose


def test_assignment_frozen_and_never_changes_hard_guards(tmp_path):
    j=Journal(tmp_path);now=time.time()
    cfg=dict(policy(),system_framework_enabled=True,framework_experiments_enabled=True)
    update(j,cfg,now)
    first=assign(j,{'order':'o','kind':'goods'},cfg)
    j.put('framework-arena',{'champion':'selective_contracts','active':False})
    assert assign(j,{'order':'o','kind':'goods'},cfg)==first
    assert set(first)=={'buyer_contract_probe_percent','framework_profile','buyer_contract_gap_power','buyer_contract_diverse_quotes'}
    assert len(j.events('framework_order'))==1


def test_buyer_diagnostics_not_blocked_by_shop_outage(tmp_path,monkeypatch):
    j=Journal(tmp_path);j.put('market_control',{'at':time.time(),'pause_restock':True,'suggestions':[]})
    stats={'samples':100,'counter_moves':10,'no_agreement':10,'success_rate':.7,'bad':0,
        'segments':{'goods':{'samples':30,'counter_moves':10,'no_agreement':10,'success_rate':.1,'bad':0}}}
    patch,reason=propose(j,'buyer',{'patch':{}},stats,{})
    assert patch=={'buyer_goods_counter_ratio':.94}


def test_immature_or_unconfirmed_quality_can_never_promote(tmp_path):
    j=Journal(tmp_path);now=time.time();arena={'experiment':'x','since':now-100}
    for i in range(100):
        j.add('framework_order',{'order':str(i),'experiment':'x','arm':'challenger','kind':'goods'},ts=now-90)
        j.add('market_result',{'order':str(i),'verified':False},ts=now-80)
    result=evaluate(j,arena,now)
    assert not result['ready'] and not result['promote']
