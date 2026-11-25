import time
from r3.state import Journal,atomic_json
from r3.contracts import prepare,observe,failed,probability


def candidate():return {'shop':'s','sku':'G1','product':{'list_price':1000},'evidence':{}}


def test_same_failed_contract_pauses_but_keeps_one_recovery_probe(tmp_path):
    j=Journal(tmp_path);c=candidate();a={'cap':500,'conditional_terms':[]}
    observe(j,{'order':'x'},c,{'grade':'C','warranty_hours':0},a,900)
    for i in range(3):failed(j,{'order':str(i),'kind':'goods'},c,'no_agreement')
    for i in range(3):failed(j,{'order':'0','kind':'goods'},c,'no_agreement')
    assert len(j.events('buyer_contract_result'))==3
    assert len(prepare([c],{'order':'new','kind':'goods'},j,{'system_framework_enabled':True,'buyer_contract_probe_percent':0}))==1
    assert c['evidence']['contract_route']['paused'] and c['evidence']['contract_route']['probe']
    assert probability(j,{'kind':'goods'},c,a,900,.25)<.1
    observe(j,{'order':'new'},c,{'grade':'A','warranty_hours':6},a,800)
    assert c['last_contract']['grade']=='A'
    assert probability(j,{'kind':'goods'},c,a,800,.25)>probability(j,{'kind':'goods'},dict(c,last_contract={'signature':'not-seen'}),a,900,.25)


def test_technical_errors_and_food_never_become_price_failures(tmp_path):
    j=Journal(tmp_path);c=candidate();observe(j,{},c,{}, {'cap':500},900)
    failed(j,{'order':'x','kind':'goods'},c,'ReadTimeout')
    failed(j,{'order':'y','kind':'food'},c,'no_agreement')
    assert not j.events('buyer_contract_result')


def test_verified_owner_continuity_survives_url_change(tmp_path):
    j=Journal(tmp_path);j.put('market-shop-identities',{'s':{'owner':'owner'},'new':{'owner':'owner'}})
    c=candidate();a={'cap':500};observe(j,{},c,{'grade':'B','warranty_hours':0},a,900)
    for i in range(3):failed(j,{'order':str(i),'kind':'goods'},c,'no_agreement')
    c['shop']='new'
    out=prepare([c],{'order':'n','kind':'goods'},j,{'system_framework_enabled':True,'buyer_contract_probe_percent':0})
    assert out[0]['evidence']['contract_route']['failed_orders']==3
