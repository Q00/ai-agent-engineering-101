"""Pre-window execution, evidence identity, cost caps and mature-only learning."""
import copy
import json
import time
from types import SimpleNamespace
import httpx
import pytest
from r3.state import Journal, atomic_json, policy, identity
from r3 import prepared, prewindow, research, strategy_lab
from r3.cycle import claim_supply, finish_supply, MarketCycle


def fixture(tmp_path):
    now=time.time(); j=Journal(tmp_path); cfg=policy()
    cfg=dict(cfg,market_cycle_enabled=True,research_enabled=True,daily_api_budget_usd=.2)
    j.put('market_rules',{'sha256':'official','compatible':True,'buyer_compatible':True})
    j.put('public_feed_caught_up_at',now);j.put('public_ledger_seq',100)
    j.put('market_control',{'at':now,'pause_restock':False,'status':'observe_and_trade'})
    atomic_json(j.md/'inventory.json',{'round':3,'balance':5000000,'start':5000000,'injected':0,'units':[],
        'restock':{'open':False,'opens_at':now+60,'closes_at':now+240},'surge':[]})
    atomic_json(j.md/'catalog.json',{'G111':{'name':'food','kind':'food','category':'food>one','list_price':1000,'shelf_hours':72}})
    atomic_json(j.md/'board.json',{'round':3,'shops':[],'buyers':[]})
    j.put('board_checked_at',now)
    for n in range(10):
        j.add('public_settle',{'buyer':'external','sku':'G111','price':850,'grade':'B','shop':'https://peer'},eid='public:'+str(n),ts=now-60-n)
    return j,cfg,now


def opinion(evidence):
    return {'summary':'Use bounded experiments; keep checkout guards.', 'hypotheses':['Demand may have shifted.'],
        'evidence_ids':evidence[:2],'skills':['stock_coverage'],'skill_steps':['measure','diagnose','propose','validate_facts'], 'buyer_probe_percent':12,'buyer_gap_power':2,
        'buyer_diverse_quotes':True,'shop_service_level':.95,'shop_plan':'diversified',
        'operating_actions':['stock_coverage','buyer_price_gap'],
        'operating_policy':{'cover_cycles':2,'initial_batch_limit':6,'frontier_min_orders':8,'recheck_seconds':120},
        'approve_trial':True}


def test_plan_prepared_while_closed_then_consumed_only_in_correct_window(tmp_path):
    j,cfg,now=fixture(tmp_path); strategy={'version':1,'patch':{}}
    packet=prewindow.prepare(j,cfg,strategy,now)
    assert packet and packet['options'][0]['buy'] and not packet['execution_allowed']
    inv=j.snapshot('inventory.json'); assert prewindow.consume(j,cfg,strategy,inv,now) is None
    inv['restock']['open']=True
    result=prewindow.consume(j,cfg,strategy,inv,now+61)
    assert result and result['buy'] and j.events('shop_prepared_plan_used')
    inv['restock']['opens_at']+=1200; inv['restock']['closes_at']+=1200
    assert prewindow.consume(j,cfg,strategy,inv,now+61) is None


@pytest.mark.parametrize('change',['rules','policy','catalog','checksum','cash','stale'])
def test_stale_or_changed_plan_falls_back_to_fresh_calculation(tmp_path,change):
    j,cfg,now=fixture(tmp_path); strategy={'version':1,'patch':{}}
    prewindow.prepare(j,cfg,strategy,now)
    inv=j.snapshot('inventory.json');inv['restock']['open']=True
    if change=='rules':j.put('market_rules',{'sha256':'new'})
    if change=='policy':cfg=dict(cfg,cash_floor=cfg['cash_floor']+1)
    if change=='catalog':atomic_json(j.md/'catalog.json',{})
    if change=='cash':inv['balance']=1
    if change=='checksum':
        packet=j.snapshot('shop-plan-v15.json');packet['selected']='wait';atomic_json(j.md/'shop-plan-v15.json',packet)
    assert prewindow.consume(j,cfg,strategy,inv,now+(100 if change=='stale' else 61)) is None


def test_preparation_during_outage_is_never_supply_authorization(tmp_path):
    j,cfg,now=fixture(tmp_path);j.put('market_control',{'pause_restock':True})
    packet=prewindow.prepare(j,cfg,{'version':1},now)
    assert packet['execution_allowed'] is False
    assert j.get('market_control')['pause_restock'] is True


def test_route_preparation_is_identical_and_invalidates_on_new_execution_evidence(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.add('attempt',{'shop':'s','sku':'G111','order':'old','kind':'rfq_failed','seconds':1},ts=now-2)
    j.add('buyer_order',{'order':'o','kind':'food','use_within_hours':3},ts=now-1)
    packet=prepared.update(j,cfg,now)
    result=prepared.get(j,{'kind':'food','use_within_hours':3},cfg,now+.01)
    from r3.buyer_routes import context
    from r3.buyer_execution import route_context
    assert result['routes']==context(j,now)
    assert result['execution']==route_context(j,now,{'kind':'food','use_within_hours':3})
    assert packet and prepared.get(j,{'kind':'food','use_within_hours':9},cfg,now) is None
    j.add('attempt',{'shop':'s','sku':'G111','order':'next','kind':'rfq_failed'},ts=now+.02)
    assert prepared.get(j,{'kind':'food','use_within_hours':3},cfg,now+.03) is None


def test_supply_timeout_never_retries_even_with_a_new_plan(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=j.snapshot('inventory.json');item={'sku':'G111','qty':1}
    claim=claim_supply(j,inv,item,'plan1');assert claim
    assert claim_supply(j,inv,item,'plan1') is None
    finish_supply(j,claim,'uncertain')
    assert claim_supply(j,inv,item,'plan2') is None


def test_confirmed_supply_results_deduplicate_but_explicit_refusal_can_retry(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=j.snapshot('inventory.json');item={'sku':'G111','qty':1}
    claim=claim_supply(j,inv,item,'one');finish_supply(j,claim,'retryable')
    claim=claim_supply(j,inv,item,'one');assert claim
    finish_supply(j,claim,'complete');assert claim_supply(j,inv,item,'one') is None
    assert claim_supply(j,inv,item,'two')


def test_analytics_excludes_own_buyer_and_keeps_observations_not_private_preferences(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.add('public_settle',{'buyer':'seochanit','sku':'G111','price':500},ts=now-1)
    data=research.analytics(j,now)
    assert data['windows']['20']['trades']==10
    assert data['buyer_preferences'][0]['buyer']=='external'
    assert data['buyer_preferences'][0]['observational_only']


class FakeAPI:
    def __init__(self):self.requests=[]
    def post(self,url,**kw):
        self.requests.append(kw['json'])
        evidence=json.loads(kw['json']['input'])['valid_evidence_ids']
        return httpx.Response(200,json={'status':'completed','usage':{'input_tokens':100,'output_tokens':100},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(opinion(evidence))}]}]})


def test_real_call_contract_usage_independent_review_and_trial_queue(tmp_path):
    j,cfg,now=fixture(tmp_path);api=FakeAPI();research.analytics(j,now)
    result=research.update(j,cfg,now,client=api,key='synthetic-test-credential')
    assert result and len(api.requests)==2 and len(j.events('model_usage'))==2
    assert result['queued_buyer_profile'] in strategy_lab.profiles(j)
    assert result['economic_superiority_verified'] is False
    assert j.get('adaptive:buyer') is None  # No direct economic promotion.
    assert j.events('research_skill_execution')
    assert research.update(j,cfg,now+1,client=api,key='synthetic-test-credential') is None
    assert len(api.requests)==2


def test_no_budget_means_no_paid_request(tmp_path):
    j,cfg,now=fixture(tmp_path);api=FakeAPI();cfg=dict(cfg,daily_api_budget_usd=0)
    research.analytics(j,now)
    assert research.update(j,cfg,now,client=api,key='synthetic-test-credential') is None
    assert api.requests==[] and j.events('market_research_deferred')


def test_explicit_observation_mode_has_no_cost_cap_but_still_tracks_calls(tmp_path):
    j,cfg,now=fixture(tmp_path);api=FakeAPI();research.analytics(j,now)
    cfg=dict(cfg,daily_api_budget_usd=None,research_budget_mode='observe')
    assert research.update(j,cfg,now,client=api,key='synthetic-test-credential')
    assert len(api.requests)==2 and len(j.events('model_usage'))==2
    from r3.usage import report
    result=report(j,cfg)
    assert result['cost_cap_usd'] is None and result['totals']['calls']==2


def test_usage_day_is_kst_and_reasoning_is_not_billed_twice(tmp_path):
    from datetime import datetime,timezone
    from r3.usage import report
    j,cfg,now=fixture(tmp_path)
    now=datetime(2026,11,11,16,0,tzinfo=timezone.utc).timestamp()
    j.add('model_usage',{'role':'one','model':'gpt-6-luna','usage':{'input_tokens':100,'output_tokens':50,
          'input_tokens_details':{'cached_tokens':20,'cache_write_tokens':10},
          'output_tokens_details':{'reasoning_tokens':30}},'estimated_usd':.001},ts=now-60)
    j.add('model_usage',{'role':'old'},ts=now-7200)
    cfg=dict(cfg,cache_prices_usd_per_million={'gpt-6-luna':[.01,.125]})
    result=report(j,cfg,now)
    assert result['date']=='2026-11-12' and result['totals']['calls']==1
    assert result['totals']['output_tokens']==50
    assert result['totals']['priced_token_estimate_usd']==pytest.approx((70*.1+20*.01+10*.125+50*.5)/1e6)


@pytest.mark.parametrize('field,value',[('buyer_probe_percent',True),('buyer_probe_percent',90),
    ('shop_service_level',float('nan')),('evidence_ids',['invented']),('skills',['shell']),('approve_trial','yes')])
def test_invalid_or_fabricated_research_never_enters_execution(tmp_path,field,value):
    j,cfg,now=fixture(tmp_path);evidence=['one','two'];candidate=opinion(evidence);candidate[field]=value
    assert not research.validate(candidate,evidence)
    assert research.publish(j,cfg,{'evidence_ids':evidence},candidate,opinion(evidence),'official',now) is None


def test_independent_critic_can_reject_all_new_trials(tmp_path):
    j,cfg,now=fixture(tmp_path);evidence=['one','two'];a=opinion(evidence);b=opinion(evidence);b['approve_trial']=False
    result=research.publish(j,cfg,{'evidence_ids':evidence},a,b,'official',now)
    assert result and result['queued_buyer_profile'] is None
    assert not strategy_lab.profiles(j) and j.get('research-shop-candidate') is None


def test_skill_compiler_always_supplies_measurement_and_final_validation(tmp_path):
    j,cfg,now=fixture(tmp_path);evidence=['one'];a=opinion(evidence)
    a['skill_steps']=['diagnose','propose','validate_facts']
    assert research.publish(j,cfg,{'evidence_ids':evidence},a,a,'official',now)
    skill=j.events('research_skill_execution')[0]['skill']
    assert skill['steps']==['measure','diagnose','propose','validate_facts']


def test_generated_profile_is_frozen_per_order_and_does_not_relax_checkout(tmp_path):
    j,cfg,now=fixture(tmp_path);p=opinion(['one'])
    name=strategy_lab.register(j,p,'official',['one'],now)
    j.put('framework-arena',{'champion':name,'active':False})
    from r3.evolution import assign
    order={'order':'o','kind':'goods'};first=assign(j,order,cfg)
    assert first['buyer_contract_probe_percent']==12
    j.put('research-buyer-candidates',{})
    assert assign(j,order,cfg)==first
    assert set(first)=={'framework_profile','buyer_contract_probe_percent','buyer_contract_gap_power','buyer_contract_diverse_quotes'}


def test_meta_identifies_sample_stagnation_and_closed_execution_guard(tmp_path):
    j,cfg,now=fixture(tmp_path)
    j.put('framework-plan',{'evaluation':{'stats':{'challenger':{'closed':83,'paid':9}}}})
    j.put('market_control',{'pause_restock':True})
    cycle=MarketCycle(j,cfg,None,None);cycle.meta(now);cycle.stop.set()
    assert 'insufficient_paid_evidence:challenger' in j.get('cycle-meta-state')['issues']
    assert 'execution_guard_closed' in j.get('cycle-meta-state')['issues']


def test_local_card_success_cannot_trap_remote_offline_recovery(tmp_path):
    from test_transport import supervisor_fixture
    calls=[]
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,lambda *a,**k:{'name':'seochanit'},
        open_tunnel=lambda port,avoid=None: calls.append(avoid) or (new,'https://new.example'))
    monitor.recovery_v15=True;monitor.provider='cloudflare';new.r3_provider='localhost'
    monitor.market.base='https://market.example'
    online=False
    def get(url,**kw):
        if url.endswith('/api/shops'):
            rows=[{'name':'peer','url':'https://peer'}]
            if online:rows.append({'name':'seochanit','url':monitor.proxy.public})
        else:
            rows=[{'name':'peer','alive':True}]+[{'name':'seochanit','alive':online,'card':{'url':monitor.proxy.public}}]
        return httpx.Response(200,json={'shops':rows})
    monitor.market.c=SimpleNamespace(get=get)
    monitor.tick(1);monitor.tick(31);monitor.tick(301)
    assert calls==['cloudflare'] and monitor.provider=='localhost'
    assert old not in stopped  # Join acceptance alone cannot retire the route.
    online=True
    for at in (361,421,481):monitor.tick(at)
    assert monitor.verified_checks>=3 and old in stopped


def test_global_market_outage_does_not_trigger_provider_churn(tmp_path):
    from test_transport import supervisor_fixture
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,lambda *a,**k:pytest.fail('must not join during outage'),
        open_tunnel=lambda *a,**k:pytest.fail('must not rotate during global outage'))
    monitor.recovery_v15=True;monitor.provider='cloudflare';monitor.market.base='https://market.example'
    monitor.market.c=SimpleNamespace(get=lambda url,**kw:httpx.Response(200,json={'shops':
        [] if url.endswith('/api/shops') else [{'name':'peer','alive':False},{'name':'seochanit','alive':False}]}))
    for at in (1,31,301,601):monitor.tick(at)
    assert monitor.procs[1] is old and not stopped


def test_automatic_failover_can_override_preferred_provider_without_changing_identity(tmp_path,monkeypatch):
    from r3 import transport
    monkeypatch.setattr(transport,'ROOT',tmp_path);monkeypatch.setenv('MARKET_TUNNEL_PROVIDER','cloudflare')
    atomic_json(tmp_path/'policy.json',{'tunnel_provider':'cloudflare','shop_recovery_v15_enabled':True})
    calls=[];process=SimpleNamespace()
    def launch(kit,port,provider):
        calls.append(provider)
        if provider=='cloudflare':raise RuntimeError('synthetic transport error')
        return process,'https://ready.example'
    monkeypatch.setattr(transport,'open_tunnel',launch)
    result,url=transport.open_available_tunnel(SimpleNamespace(log=lambda s:None),123)
    assert result is process and calls==['cloudflare','localhost']
