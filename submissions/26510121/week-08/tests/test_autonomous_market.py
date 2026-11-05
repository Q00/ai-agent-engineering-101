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
