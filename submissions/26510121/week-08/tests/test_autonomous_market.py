"""Economic invariants and closed-loop control, independent of a paid model."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3.state import Journal, policy, atomic_json
from r3.finance import sale_delta, planning_hours, supply_bound
from r3.meta import rank_gain, rank_targets
from r3.economy import restock_candidates, cash_capacity, shop_candidates


def test_book_targets_ignore_cash_to_inventory_transfer():
    b={'round':3,'shops':[{'name':'seochanit','deals':1,'profit':100,'stock_cost':1000,'book':1100},
                         {'name':'peer','deals':1,'profit':500,'stock_cost':100,'book':600}]}
    assert rank_targets(b,'shop')['book']['current']==1100
    assert rank_gain(b,'shop','seochanit',{'profit':100000})==0
    assert sale_delta(90,100)['book']==-10
    assert sale_delta(120,100,1,.1)['book']==8


def test_process_end_does_not_force_market_liquidation():
    cfg=dict(policy(), end_at='2020-01-01T00:00:00+09:00', planning_horizon_hours=6)
    assert planning_hours(cfg,time.time())==6


def test_concurrent_market_surge_cannot_spend_refund_reserves(tmp_path):
    j=Journal(tmp_path);now=time.time();cfg=policy()
    catalog={'G1':{'kind':'goods','list_price':100000,'category':'x'}}
    for n in range(80):j.add('public_settle',{'item':'G1','amount':95000,'shop':'p','buyer':'other'},ts=now-n)
    inv={'balance':1000000,'units':[],'surge':[{'sku':'G1','factor':1.5}]}
    cs=restock_candidates(inv,catalog,[],j,cfg,now)
    assert supply_bound(catalog['G1'],inv,'G1')==112500
    assert sum(c['capital_commitment'] for c in cs)<=cash_capacity(inv,j,cfg,now)['spendable']
    assert all(c['max_cost']>=c['qty']*112500 for c in cs)


def test_fresh_goods_have_hold_and_never_offer_uncompensated_loss(tmp_path):
    j=Journal(tmp_path);now=time.time();cfg=policy()
    u={'unit':'u','sku':'G1','kind':'goods','list_price':10000,'cost':8000,'grade':'A','supplied_at':now}
    cs=shop_candidates(u,{'balance':5000000},{'G1':{}},j,cfg,offer=7800,now=now)
    assert any(c['action']=='hold' for c in cs)
    assert all(c['expected_delta']['book']>=0 for c in cs if c['action']!='hold')


def test_confirmed_refund_release_has_future_cash_schedule(tmp_path):
    j=Journal(tmp_path);now=time.time()
    j.add('shop_settled',{'unit':'u','sku':'G1','price':100000,'paid_at':now,'warranty_hours':3},ts=now)
    cap=cash_capacity({'balance':5000000,'units':[]},j,policy(),now)
    assert cap['refund_liability']==100000
    assert sum(x['amount'] for x in cap['reserve_release_schedule'])==100000


def test_rule_or_feed_change_blocks_autonomous_capital_only(tmp_path):
    from r3.control import update,audit_rules
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'board.json',{'round':3,'shops':[{'name':'seochanit','book':100,'all':.5}],
                                 'buyers':[{'name':'seochanit','all':.6}],'players':[]})
    atomic_json(j.md/'inventory.json',{'round':3,'balance':5000000,'units':[]})
    atomic_json(j.md/'catalog.json',{})
    j.put('board_checked_at',now);j.put('public_feed_caught_up_at',now)
    valid='shops by book, revenue and stars; each further unit adds 0.2%, up to +50%'
    audit_rules(j,valid,now)
    assert not update(j,now=now,force=True)['pause_restock']
    audit_rules(j,'different unknown market accounting',now)
    state=update(j,now=now,force=True)
    assert state['pause_restock'] and 'rules_unverified_or_changed' in state['reasons']


def test_self_buyer_is_not_external_shop_demand(tmp_path):
    from r3.merchant import context
    j=Journal(tmp_path);now=time.time();cat={'G1':{'kind':'goods','category':'x','list_price':10000}}
    for n in range(30):j.add('public_settle',{'sku':'G1','amount':9000,'buyer':'seochanit','shop':'p'},ts=now-n)
    row=context(j,cat,now)['skus']['G1']
    assert row['public']['samples']==0 and row['arrival_rate_scenario']==0


def test_new_warranty_menu_preserves_existing_promises(tmp_path):
    j=Journal(tmp_path);now=time.time()
    u={'sku':'G1','kind':'goods','list_price':10000,'cost':6000,'grade':'A','supplied_at':now}
    cs=shop_candidates(u,{'balance':5000000},{'G1':{}},j,policy(),now=now)
    assert {0,3,6}<={c['warranty_hours'] for c in cs if c['action']!='hold'}
    u['promised_warranty_hours']=6
    assert all(c['warranty_hours']>=6 for c in shop_candidates(u,{}, {'G1':{}},j,policy(),now=now))


def test_autonomous_proposal_is_bounded_and_cannot_modify_hard_cash_rules(tmp_path):
    from r3.adaptive import propose,validate
    import pytest
    j=Journal(tmp_path)
    j.put('market_control',{'at':time.time(),'pause_restock':False,'suggestions':[
        {'role':'shop','field':'shop_margin_fraction','reason':'observed losses'}]})
    patch,reason=propose(j,'shop',{'patch':{}},{},{})
    assert patch=={'shop_margin_fraction':.03}
    with pytest.raises(ValueError):validate('shop',{'cash_reserve_fraction':0})


def test_checkout_refusal_recovers_but_uncertain_payment_never_retries(tmp_path):
    from r3.checkout import handle_order,retryable_pre_payment
    from types import SimpleNamespace
    import httpx
    assert retryable_pre_payment(['checkout refused: 409 unit unavailable'])
    assert not retryable_pre_payment(['payment failed: ReadTimeout'])
    assert not retryable_pre_payment(['payment not completed: 502'])
    for ambiguous in (False,True):
        j=Journal(tmp_path/str(ambiguous));calls=[];results=[]
        atomic_json(j.md/'catalog.json',{'G1':{'kind':'food','list_price':100}})
        shops=[{'name':s,'url':s,'pubkey':'pub','stock':{'G1':1}} for s in ['p1','p2']]
        class Market:
            def call(self,*a,**k):return httpx.Response(200,json={'shops':shops})
        def buyer(*a):
            url='p1' if not calls else 'p2'
            return {'shop':url,'receipt':{'shop':url,'item':'G1','price':50,'task':url,'sig_seller':'valid'},
                    'accept':{'cert':True,'expected_sku':'G1'}}
        def pay(o,url,pub,receipt,key,accept,why):
            calls.append(url)
            if len(calls)==1:
                why.append('payment failed: ReadTimeout' if ambiguous else 'not paying: shelf life')
                return None
            return {'ledger':42,'shop':url,'sku':'G1','value':100,'grade':'A','claim_honest':True}
        def post(m,body):
            results.append(body);return httpx.Response(200,json={'ledger':43,'verified':bool(body.get('mandate_receipt'))})
        kit=SimpleNamespace(log=lambda *a:None,fill=lambda *a,**k:'command',run_buyer=buyer,last_json=lambda x:x,
                            verify_seller=lambda *a:True,RECEIPT_KEYS=['shop','item','price','task'],
                            canon=lambda d:b'canonical',acp_pay=pay,post_result=post)
        order={'order':'o','item':'food','acceptable':['G1'],'budget':100,'kind':'food','expires':time.time()+300}
        args=(kit,j,policy(),order,{'market_buyer':{'cmd':'test'}},j.md.parent,Market(),None,None,
              {'buyer':SimpleNamespace(sign=lambda data:b'signature')})
        result=handle_order(*args)
        assert len(results)==1 and len(calls)==(1 if ambiguous else 2)
        assert result['verified'] is (not ambiguous)
        assert handle_order(*args)==result and len(results)==1
