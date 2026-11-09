"""Observed quote formats, conditional checkout requirements and mature public risk."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from r3.state import Journal,atomic_json,policy
from r3.protocol import quote,quote_terms,canon,signed_body
from r3.market_memory import buyer_assessment
from r3.buyer_quality import estimate,cohorts


def cfg():return dict(policy(),buyer_market_quality_enabled=True,buyer_contract_routing_enabled=True,buyer_counter_ratio=.92)


def task(data=None,text='',metadata=None):
    return {'id':'t','metadata':metadata or {},'status':{'state':'TASK_STATE_INPUT_REQUIRED',
            'message':{'parts':[{'text':text}]+([{'data':data}] if data else [])}}}


def setup(j,now,n=100,*,age=25000,broken=2):
    atomic_json(j.md/'catalog.json',{'G123abc':{'kind':'goods','category':'beds','list_price':10000}})
    atomic_json(j.md/'board.json',{'round':3,'buyers':[{'name':'seochanit','saved':.3,'deals':100}]})
    j.put('market_rules',{'buyer_metric':'saved','buyer_forum_metric':'raw'})
    j.put('public_feed_caught_up_at',now)
    for i in range(n):
        j.add('public_settle',{'unit':str(i),'sku':'G123abc','grade':'A','shop':'https://shop.example',
                               'amount':6500},ts=now-age-i)
        if i<broken:j.add('public_broken',{'unit':str(i),'hours_after_sale':1 if i==0 else 5,'refunded':0},ts=now-100)
    return {'sku':'G123abc','kind':'goods','category':'beds','list_price':10000}


def test_direct_data_parts_and_real_grade_now_alias_are_read():
    t=task({'ask':15920.0,'list_price':19900.0,'sku':'G022ce883','grade_now':'A',
            'warranty_hours':0,'unit':'u'},'list 19900, 39.9 hours remaining')
    assert quote(t)==15920
    terms=quote_terms(t)
    assert terms['grade']=='A' and terms['sku']=='G022ce883' and terms['unit']=='u'


@pytest.mark.parametrize('quoted,final_grade,final_warranty,price,success,reason',[
    ({},'A',0,1000,True,None),
    ({},'B',0,1000,True,None),
    ({},'B',0,6000,False,'receipt_integrity_failure'),
    ({'grade':'A','warranty_hours':6},'B',6,1000,False,'quote_grade_changed'),
    ({'grade':'A','warranty_hours':6},'A',0,1000,False,'quote_warranty_changed'),
    ({'grade':'A','warranty_hours':6},'A',6,1000,True,None),
    ({'grade':'A','warranty_hours':6},'A',6,6500,False,'receipt_integrity_failure'),
])
def test_final_signed_terms_reprice_conditional_offer_and_preserve_explicit_promises(
        tmp_path,quoted,final_grade,final_warranty,price,success,reason):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_market_contract import receipt,pub
    from market_buyer import execute
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    order={'order':'o','kind':'goods','budget':10000,'acceptable':['G123abc'],'expires':now+120}
    key=Ed25519PrivateKey.generate()
    rec=dict(receipt(key,sku='G123abc',price=price),grade=final_grade,warranty_hours=final_warranty,cert=True)
    class Peer:
        def send(self,*args):return {'id':'t','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':rec}]}]}
        def cancel(self,*args):pass
        def close(self):pass
    class Choose:
        def choose(self,role,rows,*args,**kwargs):
            return next((r for r in rows if r['id']=='accept'),rows[0])
    c={'id':'c','shop':'https://shop.example','sku':'G123abc','product':p,'reference_price':6000}
    initial=task(dict(quoted,ask=6000,sku='G123abc'));initial['id']='t'
    out=execute(order,j,Choose(),Peer,cfg(),{'version':1},
        {'https://shop.example':{'pubkey':pub(key)}},[c],now+30,{'c':(Peer(),initial)},1,{'receipt':None})
    assert bool(out['receipt']) is success
    if success:
        assert out['accept']['grades']==[final_grade]
        assert out['accept']['warranty_hours']==final_warranty
    else:
        assert j.events('attempt')[0]['reason']==reason


def test_explicit_labels_are_declarations_that_checkout_must_verify():
    t=task(text='SKU G123abc, grade B; 6 hour warranty; list price 10000; asking 7000. 20 units.')
    assert quote(t)==7000
    terms=quote_terms(t)
    assert terms['grade']=='B' and terms['warranty_hours']==6
    assert terms['declarations_require_checkout_verification']


def test_cost_budget_and_inventory_counts_cannot_become_the_price():
    with pytest.raises(ValueError,match='ambiguous_ask'):
        quote(task(text='SKU G123abc, grade A, 6h warranty, 20 units, budget 10000'))
    assert quote(task(text='ask 4000, list price 10000'))==4000


def test_conflicting_structured_asks_or_grades_fail_closed():
    with pytest.raises(ValueError,match='conflicting_ask'):
        quote(task({'ask':4000},metadata={'ask':5000}))
    with pytest.raises(ValueError,match='conflicting_offer_terms'):
        quote_terms(task({'grade_now':'B'},metadata={'grade':'A'}))


def test_claim_and_nested_offer_aliases_preserve_explicit_grade():
    assert quote_terms(task({'offer':{'price':5000,'item':'G123abc','claim':'C','warranty_hours':6}}))['grade']=='C'


def test_mature_exact_seller_sku_records_replace_sparse_blanket_stress(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    risk=estimate(j,p,'A',6,'https://shop.example',cfg(),now)
    assert risk['mature_samples']==100 and risk['observed_broken']==2
    assert .02<risk['failure_upper']<.3
    assert risk['covered_lower']==risk['failure_upper']
    assert risk['checkout_must_verify_grade'] and risk['scenario_not_causal_probability']


def test_immature_public_units_are_not_labeled_healthy(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now,age=3600)
    assert estimate(j,p,'A',6,'https://shop.example',cfg(),now)['failure_upper']==.3
    assert cohorts(j,now)['mature_goods_units']==0


def test_stale_feed_and_small_cohorts_cannot_relax_the_unknown_prior(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now,n=19,broken=0)
    assert estimate(j,p,'A',6,'https://shop.example',cfg(),now)['failure_upper']==.3
    j.put('public_feed_caught_up_at',now-181)
    assert estimate(j,p,'A',6,'https://shop.example',cfg(),now)['failure_upper']==.3


def test_mismatched_grade_does_not_borrow_the_a_cohort(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    assert estimate(j,p,'B',6,'https://shop.example',cfg(),now)['failure_upper']==.3


def test_adverse_mature_own_evidence_is_not_overruled_by_public_pool(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    j.put('quality_risk_v4',{'at':now,'grades':{'A':{'n':50,'broken':20,'within3':10}}})
    risk=estimate(j,p,'A',0,'https://shop.example',cfg(),now)
    assert risk['failure_upper']>=risk['own_risk_crosscheck']>.4


def test_partial_cover_never_assumes_all_breaks_are_refunded(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    risk=estimate(j,p,'A',3,'https://shop.example',cfg(),now)
    assert 0<risk['covered_lower']<risk['failure_upper']


def test_unknown_terms_create_explicit_checkout_conditions_not_free_quality(tmp_path):
    j=Journal(tmp_path);now=time.time();p=setup(j,now)
    result=buyer_assessment(p,{'budget':10000},{},j,cfg(),shop='https://shop.example')
    assert result['required_grade']=='A' and result['required_warranty']==6
    assert set(result['conditional_terms'])=={'grade','warranty_hours'}
    assert 0<result['failure']<.3


def test_checkout_rejects_a_different_certified_grade_under_conditional_contract(tmp_path):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_market_contract import pub
    from r3.adapter import load_kit,install
    j=Journal(tmp_path);kit=install(load_kit(),j);key=Ed25519PrivateKey.generate()
    cert={'unit':'u','sku':'G123abc','grade':'B','expires_at':None}
    co={'unit':'u','item':'G123abc','warranty_hours':6,
        'cert':{'cert':cert,'sig':base64.b64encode(key.sign(canon(cert))).decode()}}
    why=kit.check_accept({'kind':'goods','market_pubkey':pub(key)},co,
                         {'cert':True,'expected_sku':'G123abc','grades':['A'],'warranty_hours':6})
    assert 'grade B' in why


def test_loyalty_proofs_only_come_from_paid_tasks_at_the_same_shop(tmp_path):
    from r3.buyer_routes import context
    from r3.quote_market import request_text
    j=Journal(tmp_path);now=time.time()
    for o,s in [('paid','shop'),('unpaid','shop'),('other','other')]:
        j.add('agreement',{'order':o,'shop':s,'task':o},ts=now-20)
    j.add('purchase',{'order':'paid','shop':'shop','paid_at':now-10},ts=now-10)
    data=context(j,now)
    assert data['paid_task_refs']['shop']==['paid'] and 'other' not in data['paid_task_refs']
    text=request_text({'sku':'G123abc','paid_task_refs':data['paid_task_refs']['shop']},{'kind':'goods'})
    assert 'paid' in text and 'unpaid' not in text


def test_technical_route_failures_expire_and_do_not_count_price_rejections(tmp_path):
    from r3.buyer_routes import context
    j=Journal(tmp_path);now=time.time()
    for kind,age in [('ReadTimeout',10),('no_agreement',10),('ReadTimeout',1000)]:
        j.add('attempt',{'shop':'shop','kind':kind},ts=now-age)
    assert context(j,now)['errors']=={'shop':1}
