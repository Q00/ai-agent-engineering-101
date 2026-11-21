"""Maturity embargo, score-rate choices, own-skip recovery and terminal value."""
import copy
from datetime import datetime,timezone
import math
import pytest
from r3.state import atomic_json
from r3 import reversal as r
from r3.shop_offer import target_prices
from r3.economy import shop_candidates
from r3.inventory_portfolio import downside
from r3.market_memory import price_frontier
from r3.shop_growth import resale_terms
from test_market_cycle import fixture

def case(tmp_path):
    j,cfg,now=fixture(tmp_path)
    cfg=dict(cfg,reversal_v28_enabled=True)
    j.put('market_rules',{'sha256':'official','buyer_saved_formula':'margin_share_v36','buyer_metric':'saved'})
    return j,cfg,now

def test_mean_uses_a_maturity_embargo_and_independent_test():
    rows=[(i*3600,int(i%20==0)) for i in range(120)]
    m=r.validate_mean(rows)
    assert m['validated'] and m['embargo_seconds']==22200
    assert m['train']+m['test']<len(rows)
    assert m['brier']<m['upper_as_point_brier']

def test_mean_rejects_shift_and_sparse_or_nonmature_split():
    assert not r.validate_mean([(i,0) for i in range(100)])['validated']
    assert not r.validate_mean([(i*3600,0) for i in range(40)])['validated']
    rows=[(i*3600,int(i>=84)) for i in range(120)]
    assert not r.validate_mean(rows)['validated']

@pytest.mark.parametrize('change',['stale','unvalidated','partial','sparse','disabled'])
def test_seller_mean_cannot_bypass_the_evidence_gates(tmp_path,change):
    j,cfg,now=case(tmp_path)
    risk={'refund_mean':.04,'refund_upper':.2,'mature_samples':100,'mean_validated':True,'warranty_hours':6}
    assert r.mean_refund(risk,j,cfg,now)==.04
    if change=='stale':j.put('public_feed_caught_up_at',now-181)
    if change=='unvalidated':risk['mean_validated']=False
    if change=='partial':risk['warranty_hours']=3
    if change=='sparse':risk['mature_samples']=20
    if change=='disabled':cfg['reversal_v28_enabled']=False
    assert r.mean_refund(risk,j,cfg,now)==.2

def test_buyer_mean_keeps_partial_warranty_bounds(tmp_path):
    j,cfg,now=case(tmp_path)
    risk={'failure_mean':.04,'failure_upper':.2,'covered_lower':.03,'mean_validated':True,'mature_samples':100}
    assert r.buyer_failure(risk,6,j,cfg,now)==(.04,.04)
    assert r.buyer_failure(risk,3,j,cfg,now)==(.2,.03)
    risk['mean_validated']=False
    assert r.buyer_failure(risk,6,j,cfg,now)==(.2,.03)

def candidate(shop='https://peer',sku='G111',delta=-.001):
    return {'id':shop+sku,'shop':shop,'sku':sku,'reference_price':800,'score':.9,
        'product':{'kind':'goods','list_price':1000},'execution_seconds':5,
        'expected_delta':{'saved':delta,'deals':1},
        'evidence':{'seller_term_prior':{'grade':'A','warranty_hours':6}}}

def test_routing_uses_incremental_saved_instead_of_absolute_saved(tmp_path):
    j,cfg,now=case(tmp_path)
    bad=candidate();good=candidate('https://other',delta=.001);good['score']=.8
    assert r.route_key(good,j,cfg,now)>r.route_key(bad,j,cfg,now)
    good['execution_seconds']=100
    faster=copy.deepcopy(good);faster['execution_seconds']=5
    assert r.route_key(faster,j,cfg,now)>r.route_key(good,j,cfg,now)

def seed_skips(j,cfg,now,c):
    a={'required_grade':'A','required_warranty':6,'saved_state':{'score_formula':'margin_share_v36'}}
    for i in range(3):r.record_skip(j,{'order':'o'+str(i),'kind':'goods'},c,a,800,'t'+str(i),now-30+i)

def test_own_skips_pause_only_comparable_routes_and_price_improvement_recovers(tmp_path):
    j,cfg,now=case(tmp_path);cfg['buyer_contract_probe_percent']=0
    c=candidate();seed_skips(j,cfg,now,c);other=candidate('https://other')
    order={'order':'new','kind':'goods'}
    assert r.prune_routes([c,other],order,j,cfg,now)==[other]
    assert all(e['not_seller_refusal'] for e in j.events('buyer_economic_skip_v28'))
    c['reference_price']=700
    assert c in r.prune_routes([c,other],order,j,cfg,now)

@pytest.mark.parametrize('change',['expired','new_terms','paid','positive_objective'])
def test_route_pause_has_observed_recovery_paths(tmp_path,change):
    j,cfg,now=case(tmp_path);cfg['buyer_contract_probe_percent']=0;c=candidate();seed_skips(j,cfg,now,c)
    if change=='expired':now+=121
    if change=='new_terms':c['evidence']['seller_term_prior']['grade']='B'
    if change=='paid':j.add('purchase',{'order':'paid','shop':c['shop'],'sku':c['sku'],'paid_at':now-1},ts=now-1)
    if change=='positive_objective':c['expected_delta']['saved']=.001
    assert c in r.prune_routes([c,candidate('https://other')],{'order':'new','kind':'goods'},j,cfg,now)

def test_transport_failures_do_not_create_economic_refusal_evidence(tmp_path):
    j,cfg,now=case(tmp_path);c=candidate()
    for i in range(5):j.add('attempt',{'order':str(i),'shop':c['shop'],'kind':'ConnectError'},ts=now-i)
    assert r.prune_routes([c],{'order':'new','kind':'goods'},j,cfg,now)==[c]
    assert not j.events('buyer_economic_skip_v28')

def test_personal_target_requires_market_bound_identity(tmp_path):
    j,cfg,now=case(tmp_path)
    atomic_json(j.md/'board.json',{'buyers':[{'name':'verified','saved':.74,'deals':100},
        {'name':'other','saved':.6,'deals':100},{'name':'third','saved':.65,'deals':100}]})
    u={'list_price':1000,'cost':600};risk={'failure_upper':0,'covered_lower':0}
    anon={'identity_status':'anonymous','customer_profile':{'market_buyers':['verified']}}
    auth=dict(anon,identity_status='verified_key')
    base=target_prices(u,risk,j,customer_memory=anon)
    personalized=target_prices(u,risk,j,customer_memory=auth)
    assert 704 not in base and 704 in personalized

def test_terminal_clearance_never_assumes_stock_becomes_worthless(tmp_path):
    j,cfg,now=case(tmp_path)
    u={'unit':'u','sku':'G111','kind':'goods','grade':'A','list_price':200000,'cost':100000,'supplied_at':now-100*3600}
    cfg=dict(cfg,warranty_menu_enabled=False,shop_championship_enabled=False,book_growth_v23_enabled=False,
        goods_warranty_by_grade={'A':0},system_framework_enabled=False,objective_feedback_enabled=False,
        market_end_at=datetime.fromtimestamp(now+3600,timezone.utc).isoformat())
    choices=shop_candidates(u,{'balance':10000000},j.snapshot('catalog.json'),j,cfg,now=now)
    quotes=[c for c in choices if c['action']!='hold']
    assert quotes and all(c['recovery_floor']==math.ceil(100000*(1-.01/24)) for c in quotes)

def test_inventory_stress_keeps_upper_refund_risk_when_mean_is_smaller():
    c={'evidence':{'kind':'goods','list_price':1000,'scenario_unit_cost':600,'arrival_rate_scenario':10,
        'refund_stress':.03,'refund_upper_scenario':.2,'growth':{'positive_paid_while_available':10}}}
    assert downside(c)['evidence']['refund_stress']==.2 and c['evidence']['refund_stress']==.03

def test_terminal_observer_is_bounded_and_expires(tmp_path):
    j,cfg,now=case(tmp_path)
    cfg['market_end_at']=datetime.fromtimestamp(now+3600,timezone.utc).isoformat()
    atomic_json(j.md/'board.json',{'buyers':[{'name':'seochanit','saved':.7,'deals':100,'forum':.9}]})
    j.add('purchase',{'order':'paid','paid_at':now-10},ts=now-10)
    row=r.update(j,cfg,now)
    assert row['remaining_hours']==pytest.approx(1) and len(row['buyer_saved_scenarios'])==4
    assert row['buyer_forum_max_gain']==pytest.approx(.03)
    assert r.context(j,now) and not r.context(j,now+181)
    import json
    assert len(json.dumps(r.brief(row)).encode())<800

def test_price_tail_has_an_observed_smaller_segment_and_preserves_ties():
    assert price_frontier([800]*11)==[]
    rows=price_frontier(list(range(100,120)))
    assert rows[0]=={'quantile':.75,'price':114,'n_ge':6}
    assert rows[1]=={'quantile':.9,'price':117,'n_ge':3}
    assert price_frontier([800]*20)[0]['n_ge']==20

def test_high_price_contract_does_not_claim_all_historical_traffic(tmp_path):
    j,cfg,now=case(tmp_path)
    public={'median':700,'term_prices':[{'grade':g,'warranty_hours':6,'n':20,'median':700,
        'price_frontier':[{'quantile':.75,'price':850,'n_ge':5}]} for g in 'ABC']}
    p={'kind':'goods','list_price':1000}
    median=resale_terms(p,public,j,cfg,now)
    upper=resale_terms(p,public,j,cfg,now,quantile=.75)
    assert upper['price']>median['price'] and upper['observed_price_segment_fraction']==pytest.approx(.25)

def test_slow_observers_finish_before_current_market_facts_are_published(tmp_path,monkeypatch):
    from r3 import cycle,quality,feedback,research,prepared,prewindow,adaptive,usage
    j,cfg,now=case(tmp_path);order=[]
    cfg=dict(cfg,service_contract_v26_enabled=False,shop_reputation_v24_enabled=False,
        book_growth_v23_enabled=False,buyer_joint_objective_enabled=False,objective_decision_enabled=False)
    for module,name,label in [(r,'update','terminal'),(quality,'update','quality'),
        (feedback,'update','compiler'),(research,'analytics','market')]:
        monkeypatch.setattr(module,name,lambda *a,_label=label,**kw:order.append(_label))
    monkeypatch.setattr(prepared,'update',lambda *a:None)
    monkeypatch.setattr(prewindow,'prepare',lambda *a:None)
    monkeypatch.setattr(adaptive,'effective',lambda *a,**kw:(cfg,{'version':1}))
    monkeypatch.setattr(usage,'report',lambda *a:None)
    c=cycle.MarketCycle(j,cfg,None,None);monkeypatch.setattr(c,'meta',lambda *a:None)
    c.prepare()
    assert order.index('quality')<order.index('market')<order.index('compiler')

def test_forum_chooses_a_less_recently_served_category_with_real_refs(tmp_path):
    from r3.buyer_forum import schedule
    j,cfg,now=case(tmp_path)
    cat=j.snapshot('catalog.json');cat['G222']=dict(cat['G111'],name='other',category='food>other')
    atomic_json(j.md/'catalog.json',cat);book={}
    for sku,ledger,price in [('G111',103,600),('G222',203,700)]:
        j.add('purchase',{'order':sku,'sku':sku,'kind':'food','grade':'A','warranty_hours':0,
            'price':price,'value':1000,'ledger':ledger,'paid_at':now-20},ts=now-20)
        book[sku]={'term_prices':[{'grade':'A','warranty_hours':0,'n':3,'median':800,'refs':[ledger-1,ledger-2]}]}
    j.put('buyer-insight-audience:v28',{'food>one':now-60})
    cfg=dict(cfg,objective_decision_enabled=False,buyer_championship_enabled=False)
    schedule(j,cfg,book,cat,now)
    assert 203 in j.events('buyer_insight_queued')[0]['refs']

def test_supervisor_survives_failure_of_its_own_error_logging(tmp_path,monkeypatch):
    import sqlite3
    from r3.cycle import MarketCycle
    j,cfg,now=case(tmp_path);c=MarketCycle(j,cfg,None,None);calls=[]
    def tick():
        calls.append(1)
        if len(calls)==3:c.stop.set()
        raise sqlite3.OperationalError('database is locked')
    def add(*a,**kw):raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(c,'tick',tick);monkeypatch.setattr(j,'add',add)
    monkeypatch.setattr(c.stop,'wait',lambda *a:None)
    c.run()
    assert len(calls)==3

def test_a_dead_scheduler_can_restart_without_a_second_live_thread(tmp_path,monkeypatch):
    from r3 import cycle
    from types import SimpleNamespace
    j,cfg,now=case(tmp_path);c=cycle.MarketCycle(j,cfg,None,None);started=[]
    c.thread=SimpleNamespace(is_alive=lambda:False)
    def thread(**kwargs):return SimpleNamespace(start=lambda:started.append(kwargs),is_alive=lambda:True)
    monkeypatch.setattr(cycle.threading,'Thread',thread)
    c.start(('a','b'));c.start(('a','b'))
    assert len(started)==1 and c.args==('a','b')
