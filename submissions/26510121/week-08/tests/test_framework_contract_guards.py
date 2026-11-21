import time
from r3.state import Journal,atomic_json,policy
from seller_r3 import Seller
from r3.adaptive import begin_buyer,evidence,MATURITY
from r3.outcomes import reconcile


def test_shop_respects_explicit_required_grade_without_treating_conditional_rfq_as_requirement(tmp_path):
    j=Journal(tmp_path);now=time.time()
    units=[{'unit':'c','sku':'G111','name':'bed','kind':'goods','category':'bed','grade':'C','supplied_at':now-100,'list_price':1000},
           {'unit':'a','sku':'G111','name':'bed','kind':'goods','category':'bed','grade':'A','supplied_at':now,'list_price':1000}]
    atomic_json(j.md/'inventory.json',{'round':3,'units':units})
    seller=Seller(j)
    assert seller.select_unit('I require certified grade A for G111.','one')['unit']=='a'
    assert seller.select_unit('G111: state grade. Without declared grade my offer is conditional on certified grade A.','two')['unit']=='c'


def test_explicit_longer_warranty_is_not_silently_shortened(tmp_path):
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'inventory.json',{'round':3,'units':[{'unit':'a','sku':'G111','name':'bed','kind':'goods',
        'category':'bed','grade':'A','supplied_at':now,'list_price':1000}]})
    seller=Seller(j)
    assert seller.select_unit('G111, at least 12 hours warranty.','one')['minimum_warranty_hours']==12
    assert seller.select_unit('G111, at least 100 hours warranty.','two') is None


def test_final_refund_enters_segmented_adaptive_evidence(tmp_path):
    j=Journal(tmp_path);now=time.time();at=now-MATURITY-1000
    j.add('buyer_order',{'order':'o','kind':'goods','budget':1000,'strategy_version':1},ts=at)
    p={'order':'o','kind':'goods','unit':'u','paid_at':at+10,'price':100,'value':200,'warranty_hours':6}
    j.add('purchase',p,ts=at+10)
    j.add('outcome',{'order':'o','broken':True,'refund_status':'expected_by_warranty_rule'},ts=at+100)
    j.add('public_broken',{'order':'o','unit':'u','hours_after_sale':1,'refunded':100},ts=at+100)
    reconcile(j,[p],now)
    e=evidence(j,'buyer',1,now)
    assert e['paid']==1 and e['bad']==1 and e['segments']['goods']['samples']==1
