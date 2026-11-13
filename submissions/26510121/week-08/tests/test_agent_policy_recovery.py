import asyncio
import copy
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from r3.state import atomic_json, identity
from r3 import feedback, research
from r3.discovery import DiscoveryCache, install
from r3.recovery import request, receive
from r3.shop_growth import target_stock
from shop_recovery_guard import decide
from test_market_cycle import opinion, FakeAPI
from test_objective_feedback import market


def test_agents_select_operating_parameters_and_main_uses_consensus(tmp_path):
    j,cfg,now=market(tmp_path);cfg=dict(cfg,agent_policy_selection_enabled=True)
    a=opinion(['one']);b=opinion(['one']);a['approve_trial']=b['approve_trial']=False
    a['operating_policy']={'cover_cycles':3,'initial_batch_limit':20,'frontier_min_orders':4,'recheck_seconds':300}
    b['operating_policy']={'cover_cycles':2,'initial_batch_limit':10,'frontier_min_orders':8,'recheck_seconds':60}
    row=research.publish(j,cfg,{'evidence_ids':['one']},a,b,'rules',now)
    assert row['operating_policy']=={'cover_cycles':2,'initial_batch_limit':10,'frontier_min_orders':8,'recheck_seconds':60}
    m=j.get('cycle-market-analytics');m['top_skus']=[{'sku':'Ghot','recent20':70,'recent60':100,'buyers20':8},
        {'sku':'Gweak','recent20':10,'recent60':20,'buyers20':3}];j.put('cycle-market-analytics',m)
    p=feedback.update(j,cfg,now);s=feedback.apply(j,cfg,'shop',now=now);buyer=feedback.apply(j,cfg,'buyer',order='o',now=now)
    assert p['policy_authority']=='agent_selected' and s['shop_demand_horizon_cycles']==2
    assert s['shop_public_demand_caps']=={'Ghot':10,'Gweak':6}
    assert target_stock(1000,0,s,True,public_limit=s['shop_public_demand_caps']['Ghot'])==10
    assert buyer['buyer_frontier_min_orders']==8 and buyer['buyer_frontier_recheck_seconds']==60
    assert buyer['buyer_saved_target_margin']==cfg['buyer_saved_target_margin']
    assert row['trial_review_approved'] is False and row['queued_buyer_profile'] is None


@pytest.mark.parametrize('bad',[None,{'cover_cycles':3},
    {'cover_cycles':float('inf'),'initial_batch_limit':20,'frontier_min_orders':4,'recheck_seconds':60},
    {'cover_cycles':True,'initial_batch_limit':20,'frontier_min_orders':4,'recheck_seconds':60},
    {'cover_cycles':3,'initial_batch_limit':20,'frontier_min_orders':0,'recheck_seconds':60}])
def test_agent_policy_cannot_escape_typed_parameter_space(bad):
    value=opinion(['one']);value['operating_policy']=bad
    assert not research.validate(value,['one'])


@pytest.mark.parametrize('key,value',[('buyer_frontier_min_orders',6.5),('buyer_frontier_recheck_seconds',60.5)])
def test_compiled_integer_controls_cannot_accept_fractional_values(key,value):
    assert not feedback.valid_patch('buyer',{key:value})


def test_concurrent_feed_timestamp_does_not_misclassify_current_facts(tmp_path):
    j,cfg,now=market(tmp_path);j.put('public_feed_caught_up_at',now+.5)
    assert feedback.fresh(j,now)
    j.put('public_feed_caught_up_at',now+10)
    assert not feedback.fresh(j,now)


def test_stale_analytics_does_not_spend_api_budget_just_because_cursor_caught_up(tmp_path):
    j,cfg,now=market(tmp_path);cfg=dict(cfg,agent_policy_selection_enabled=True)
    j.put('cycle-market-analytics',{'at':now,'feed_at':now-1000,'windows':{}})
    api=FakeAPI()
    assert research.update(j,cfg,now,client=api,key='synthetic') is None
    assert api.requests==[] and not j.events('model_usage')


def test_reasoner_receives_explicit_baseline_instead_of_guessing_first_enum(tmp_path):
    j,cfg,now=market(tmp_path);cfg=dict(cfg,agent_policy_selection_enabled=True)
    j.put('cycle-market-analytics',{'at':now,'feed_at':now,'windows':{}})
    api=FakeAPI();assert research.update(j,cfg,now,client=api,key='synthetic')
    facts=json.loads(api.requests[0]['input'])['facts']
    assert facts['current_operating_policy']=={'cover_cycles':1,'initial_batch_limit':6,'frontier_min_orders':8,'recheck_seconds':120}


def test_one_failed_analytical_role_does_not_stop_other_roles_or_preparation(tmp_path,monkeypatch):
    from r3.cycle import MarketCycle
    from r3 import quality,prepared,prewindow
    j,cfg,now=market(tmp_path);calls=[]
    monkeypatch.setattr(quality,'update',lambda *a:(_ for _ in ()).throw(ValueError('synthetic bad review')))
    monkeypatch.setattr(feedback,'update',lambda *a,**k:calls.append('compiled'))
    monkeypatch.setattr(prepared,'update',lambda *a:calls.append('buyer_prepared'))
    monkeypatch.setattr(prewindow,'prepare',lambda *a:calls.append('shop_prepared'))
    cycle=MarketCycle(j,cfg,None,None);cycle.prepare();cycle.stop.set()
    assert calls==['compiled','buyer_prepared','shop_prepared']
    statuses=j.get('analytical-role-status')
    assert statuses['quality_evaluator']['status']=='failed' and statuses['policy_compiler']['status']=='completed'
    assert j.events('analytical_role_error')[0]['role']=='quality_evaluator'


def probe(**patch):
    return dict(runtime_matches=True,verified=True,identity_matches=True,public_ready=True,
        registry_count=10,active_peers=8,shop_alive=False,listed=False,primary_age=10,
        cooperative_recovery=True,unverified_seconds=300,**patch)


def test_fresh_primary_heartbeat_cannot_silence_confirmed_observer_failure():
    action,state,_=decide(probe(),{},1000);assert action=='wait'
    action,state,_=decide(probe(),state,1030);assert action=='request'
    assert decide(probe(),state,1060)[0]=='wait'


def test_new_address_gets_discovery_grace_and_global_outage_never_queues():
    p=probe();p['unverified_seconds']=30
    assert decide(p,{'misses':5},1000)[0]=='wait'
    p=probe();p['active_peers']=0
    assert decide(p,{'misses':5},1000)[0]=='wait'


@pytest.mark.parametrize('fault',['session','url','expired','future','duplicate','tampered'])
def test_old_or_forged_recovery_request_cannot_mutate_current_registration(tmp_path,fault):
    runtime={'session':'current'};url='https://own.example';rid=request(tmp_path,runtime,url,probe(),1000)
    assert rid and receive(tmp_path,runtime,url,1001)
    duplicate=None;at=1001
    if fault=='session':runtime={'session':'new'}
    if fault=='url':url='https://new.example'
    if fault=='expired':at=1090
    if fault=='future':at=999
    if fault=='duplicate':duplicate=rid
    if fault=='tampered':
        raw=json.loads((tmp_path/'recovery-request.json').read_text());raw['action']='rotate_everything';atomic_json(tmp_path/'recovery-request.json',raw)
    assert receive(tmp_path,runtime,url,at,duplicate) is None


def test_primary_consumes_request_only_after_its_own_public_probes(tmp_path):
    from test_transport import supervisor_fixture
    attempts=[]
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,lambda *a,**k:attempts.append(a) or {'name':'seochanit'})
    monitor.cooperative=True;monitor.runtime={'session':'current'};monitor.provider='cloudflare';monitor.market.base='https://market.example'
    def get(url,**kwargs):
        rows=([{'name':'seochanit','url':'https://old.example'}] if url.endswith('/api/shops') else
              [{'name':'seochanit','alive':False,'card':{'url':'https://old.example'}},{'name':'peer','alive':True}])
        return httpx.Response(200,json={'shops':rows})
    monitor.market.c=SimpleNamespace(get=get)
    rid=request(tmp_path/'.market',monitor.runtime,monitor.proxy.public,probe(),time.time())
    monitor.tick(1)
    assert attempts and monitor.last_request==rid and monitor.procs[1] is old and not stopped
    ack=json.loads((tmp_path/'.market/recovery-response.json').read_text())
    assert ack['accepted'] is True


def card_proxy():
    def rewrite(card):
        for iface in card['supportedInterfaces']:iface['url']=p.public+'/a2a/0'
        return card
    p=SimpleNamespace(public='https://old.example',upstream='http://127.0.0.1:9000',rewrite=rewrite)
    return p


def test_discovery_uses_separate_pool_and_rewrites_rotated_address_without_corrupting_cache():
    async def run():
        calls=[]
        class Client:
            async def get(self,url):
                calls.append(url)
                return httpx.Response(200,json={'name':'Seochanit Shop','supportedInterfaces':[{'url':'http://127.0.0.1:9000/a2a'}]},request=httpx.Request('GET',url))
        cache=DiscoveryCache(factory=Client);p=card_proxy()
        a=await cache.card(p);p.public='https://new.example';b=await cache.card(p)
        assert len(calls)==1 and cache.hits==1
        assert json.loads(a.body)['supportedInterfaces'][0]['url']=='https://old.example/a2a/0'
        assert json.loads(b.body)['supportedInterfaces'][0]['url']=='https://new.example/a2a/0'
        assert cache.cached['supportedInterfaces'][0]['url'].startswith('http://127.0.0.1')
    asyncio.run(run())


def test_expired_discovery_does_not_hide_dead_seller_and_failure_retry_is_bounded():
    async def run():
        clock=[0];calls=[]
        class Client:
            async def get(self,url):
                calls.append(url)
                if len(calls)>1:raise httpx.ReadTimeout('synthetic')
                return httpx.Response(200,json={'name':'Seochanit Shop'},request=httpx.Request('GET',url))
        cache=DiscoveryCache(factory=Client,clock=lambda:clock[0]);p=card_proxy();p.rewrite=lambda c:c
        assert (await cache.card(p)).status_code==200
        clock[0]=11
        assert (await cache.card(p)).status_code==503
        clock[0]=11.5
        assert (await cache.card(p)).status_code==503 and len(calls)==2
    asyncio.run(run())


def test_card_path_does_not_wait_for_negotiation_pool_and_checkout_route_is_unchanged():
    async def run():
        from starlette.responses import JSONResponse
        blocked=asyncio.Event()
        class Base:
            async def handle(self,req):
                if req.path_params['path'].startswith('a2a/'):
                    await blocked.wait()
                return JSONResponse({'original_checkout':True})
        kit=SimpleNamespace(Proxy=Base);install(kit);p=kit.Proxy.__new__(kit.Proxy)
        p.upstream='http://127.0.0.1:9000';p.acp=object()
        class Cache:
            async def card(self,proxy):return JSONResponse({'name':'Seochanit Shop'})
        p.discovery=Cache()
        task=asyncio.create_task(p.handle(SimpleNamespace(method='POST',path_params={'path':'a2a/0'})))
        result=await asyncio.wait_for(p.handle(SimpleNamespace(method='GET',path_params={'path':'.well-known/agent-card.json'})),.1)
        assert result.status_code==200 and not task.done()
        checkout=await p.handle(SimpleNamespace(method='POST',path_params={'path':'acp/checkout_sessions'}))
        assert json.loads(checkout.body)=={'original_checkout':True}
        blocked.set();await task
    asyncio.run(run())
