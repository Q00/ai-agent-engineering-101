"""Meaningful execution invariants: bounded races, Saved/time, idempotent quality."""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import pytest
from r3.state import Journal,policy,atomic_json
from r3.model import Chooser
from r3.quote_market import race_quotes,close_quotes
from r3.transport import ShopSupervisor
from r3.buyer_execution import route_context,route_adjust


def config():return dict(policy(),buyer_throughput_enabled=True,buyer_patch34_enabled=True,buyer_counter_ratio=.92)


def test_maintenance_gate_precedes_negotiation_and_self_expires(tmp_path,monkeypatch):
    from market_buyer import maintenance_wait
    j=Journal(tmp_path);now=time.time()
    j.put('buyer_maintenance',{'token':'drain','until':now+10})
    def release(seconds):
        assert j.get('buyer_maintenance_ready')['order']=='o'
        assert not j.events('buyer_order') and not j.events('agreement')
        j.put('buyer_maintenance',{})
    monkeypatch.setattr('market_buyer.time.sleep',release)
    assert maintenance_wait(j,{'order':'o','expires':now+300})
    j.put('buyer_maintenance',{'token':'stop','until':now+10})
    atomic_json(j.md/'runtime.json',{'session':'s','pid':1})
    atomic_json(j.md/'runtime-stop.json',{'session':'s','pid':1})
    assert not maintenance_wait(j,{'order':'o','expires':now+300})
    j.put('buyer_maintenance',{'token':'expired','until':now-1})
    assert maintenance_wait(j,{'order':'o','expires':now+300})


def test_reconcile_reads_purchases_once_and_preserves_late_break(tmp_path,monkeypatch):
    j=Journal(tmp_path);now=time.time()
    for i in range(30):
        j.add('purchase',{'order':str(i),'name':str(i),'paid_at':now-25000,'price':100,'value':200,'kind':'goods'})
    j.reconcile(now)
    for i in range(20):j.add('broken_news',{'name':str(i),'broke_at':now-24000})
    calls=[];original=j.events
    def counted(kind,*args):calls.append(kind);return original(kind,*args)
    monkeypatch.setattr(j,'events',counted)
    j.reconcile(now);assert calls.count('purchase')==1
    before=len(original('outcome'));j.reconcile(now)
    assert len(original('outcome'))==before==50
    assert sum(bool(r.get('broken')) for r in original('outcome'))==20
    assert j.get('offset:payments.jsonl') is None


def test_saved_progress_per_time_beats_slow_marginal_discount(tmp_path):
    j=Journal(tmp_path);cfg=config()
    atomic_json(j.md/'board.json',{'round':3,'buyers':[
        {'name':'seochanit','saved':.3,'deals':100,'forum':.8,'posts':10},
        {'name':'peer','saved':.4,'deals':200,'forum':.8,'posts':20}]})
    j.put('market_rules',{'buyer_metric':'saved','buyer_forum_metric':'raw'})
    candidates=[{'id':'accept','score':.4,'execution_seconds':3,'expected_delta':{'saved':.001,'deals':1}},
                {'id':'counter','score':.5,'execution_seconds':15,'expected_delta':{'saved':.0012,'deals':1}}]
    chosen=Chooser(j,cfg).choose('buyer',candidates,{'buyer_execution':True,'operation':'price'})
    assert chosen['id']=='accept'


def test_route_prior_cannot_manufacture_more_than_one_purchase(tmp_path):
    j=Journal(tmp_path);now=time.time()
    for i in range(10):j.add('purchase',{'order':str(i),'shop':'s','sku':'G1','paid_at':now})
    context=route_context(j,now)
    c={'shop':'s','sku':'G1','score':.5,'expected_delta':{'deals':.7,'saved':.001},'evidence':{}}
    route_adjust(c,{},j,config(),context)
    assert 0<=c['expected_delta']['deals']<=1
    assert c['evidence']['execution_model']['route_samples']==10


def test_execution_route_model_separates_food_use_requirements(tmp_path):
    j=Journal(tmp_path);now=time.time()
    j.add('attempt',{'order':'long','shop':'s','sku':'G1','kind':'no_agreement','use_within_hours':48},ts=now-10)
    j.add('purchase',{'order':'short','shop':'s','sku':'G1','paid_at':now-10,'expires_at':now+3600},ts=now-10)
    assert route_context(j,now,{'kind':'food','use_within_hours':24})['routes']=={}
    assert route_context(j,now,{'kind':'food','use_within_hours':.5})['routes'][('s','G1')]['paid']==1


def test_fast_quote_race_does_not_wait_for_a_stalled_peer(tmp_path):
    j=Journal(tmp_path);cfg=dict(config(),buyer_rfq_food_seconds=.5,buyer_compare_grace_seconds=.02)
    atomic_json(j.md/'board.json',{'round':3,'buyers':[{'name':'seochanit','saved':.3,'deals':100}]})
    j.put('market_rules',{'buyer_metric':'saved'})
    stalled=threading.Event();release=threading.Event();closed=threading.Event()
    class Peer:
        def discover(self,shop):self.shop=shop
        def send(self,text,*args):
            if self.shop=='slow':stalled.set();release.wait(2)
            return {'id':self.shop,'status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{
                'metadata':{'ask':500,'sku':'G1','expires_at':time.time()+36000}}}}
        def cancel(self,*args):pass
        def close(self):
            if self.shop=='slow':closed.set()
    rows=[{'id':s,'shop':s,'sku':'G1','reference_price':500,'score':.2,'expected_delta':{},
           'product':{'kind':'food','list_price':1000},'evidence':{}} for s in ['slow','fast']]
    start=time.monotonic()
    try:
        prepared,contacted=race_quotes({'order':'o','kind':'food','budget':1000},rows,
            {'slow':{'pubkey':'x'},'fast':{'pubkey':'x'}},j,cfg,Peer,time.time()+10)
        assert stalled.is_set() and not release.is_set()
        assert time.monotonic()-start<1
        assert contacted==2 and set(prepared)=={'fast'} and [c['id'] for c in rows]==['fast']
        assert rows[0]['fast_ready']
        close_quotes(prepared)
    finally:release.set()
    assert closed.wait(1)


@pytest.mark.parametrize('active_peer',[True,False])
def test_board_offline_repairs_same_address_without_global_rejoin_storm(tmp_path,active_peer):
    calls=[];old=SimpleNamespace(poll=lambda:None)
    monitor=ShopSupervisor(SimpleNamespace(card_ok=lambda url:True,stop=lambda p:pytest.fail('keep tunnel'),
        log=lambda s:None),SimpleNamespace(tunnel=True,name='seochanit'),{},tmp_path,
        SimpleNamespace(join=lambda *a,**k:calls.append(a) or {'name':'seochanit'}),
        {'shop':'existing-key'},[old,old],SimpleNamespace(public='https://old.example'),1,2,{},now=0,provider='cloudflare')
    monitor.market.base='https://market.example'
    def get(url,**kwargs):
        rows=[{'name':'seochanit','url':'https://old.example','alive':False},
              {'name':'peer','alive':active_peer}]
        return httpx.Response(200,json={'round':3,'shops':rows})
    monitor.market.c=SimpleNamespace(get=get)
    monitor.tick(1);assert not calls
    monitor.tick(61)
    assert len(calls)==int(active_peer)
    if calls:assert calls[0][2:] == ('existing-key','https://old.example')
    monitor.tick(121);assert len(calls)==int(active_peer)


def test_pending_board_repair_stops_retrying_during_global_outage(tmp_path):
    calls=[];old=SimpleNamespace(poll=lambda:None);peer_alive=[True]
    def join(*a,**k):calls.append(a);raise SystemExit('temporary validator outage')
    monitor=ShopSupervisor(SimpleNamespace(card_ok=lambda url:True,stop=lambda p:None,log=lambda s:None),
        SimpleNamespace(tunnel=True,name='seochanit'),{},tmp_path,SimpleNamespace(join=join),
        {'shop':'key'},[old,old],SimpleNamespace(public='https://old.example'),1,2,{},now=0,provider='cloudflare')
    monitor.market.base='https://market.example'
    monitor.market.c=SimpleNamespace(get=lambda *a,**k:httpx.Response(200,json={'shops':[
        {'name':'seochanit','url':'https://old.example','alive':False},
        {'name':'peer','alive':peer_alive[0]}]}))
    monitor.tick(1);monitor.tick(61)
    assert monitor.pending_registration and len(calls)==1
    peer_alive[0]=False;monitor.tick(121)
    assert len(calls)==1 and monitor.board_global_outage
