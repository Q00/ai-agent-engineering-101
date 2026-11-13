"""Explicit services, exact continuation labels and bounded contract confirmation."""
import copy
import time
from types import SimpleNamespace
import pytest
from r3.state import Journal,atomic_json,policy
from r3 import service_contracts as sc,service_policy as sp,negotiation_path as np
from r3.market_memory import buyer_price_actions,buyer_assessment
from r3.shop_growth import marginal
from r3.quote_market import clarify
from test_market_cycle import fixture
from seller_r3 import Seller


def food(now,uid,left):
    return {'unit':uid,'sku':'G111','kind':'food','name':'food','category':'food>one',
        'list_price':1000,'cost':500,'expires_at':now+left*3600,'supplied_at':now-10}


@pytest.mark.parametrize('text,hours',[
    ('Food must last at least 12 hours after payment',12),
    ('minimum shelf life after checkout: 2 days',48),
    ('I need 5 hours of shelf life',5),('Use within 3h',3)])
def test_explicit_use_units(text,hours):
    s=sc.parse(text);assert s['valid'] and s['minimum_hours']==hours and s['explicit_use']


def test_followup_only_tightens_and_does_not_read_articles_as_grades():
    old=sc.parse('I need a quote for G111. Food must last at least 4 hours. minimum grade B')
    assert old['grades']==['A','B'] and old['sku']=='G111'
    new=sc.parse('Food must last at least 8 hours. require grade A',old)
    assert new['minimum_hours']==8 and new['grades']==['A']
    assert sc.parse('800',new)==new
    assert sc.parse('Please state grade and warranty',new)['valid']
    assert not sc.parse('only grade C',new)['valid']
    assert not sc.parse('I want G222 instead',new)['valid']


@pytest.mark.parametrize('text',['Food must last at least -2h','at least 73h warranty'])
def test_invalid_requirements_are_rejected(text):assert not sc.parse(text)['valid']


def test_eligibility_has_checkout_time_buffer_and_record_dedup(tmp_path):
    j,cfg,now=fixture(tmp_path);cat=j.snapshot('catalog.json');u=food(now,'u',12)
    s=sc.parse('G111 Food must last at least 12h')
    assert not sc.eligible(u,cat,s,now)
    s=sc.parse('G111 Food must last at least 8h')
    assert sc.eligible(u,cat,s,now)
    sc.record(j,'t',s,u,now);sc.record(j,'t',s,u,now+10)
    assert len(j.events('shop_service_need'))==1
    assert 'text' not in j.events('shop_service_need')[0]


def test_declared_flexible_grade_protects_scarce_fresh_stock(tmp_path):
    j,cfg,now=fixture(tmp_path);cat=j.snapshot('catalog.json')
    a,b=food(now,'A',65),food(now,'B',40);units=[a,b]
    buckets=[{'minimum_hours':48,'grades':None,'weight':3}]
    explicit=sc.parse('G111 minimum grade B Food must last at least 4h')
    unknown=sc.parse('G111 Food must last at least 4h')
    assert min(units,key=lambda u:sc.priority(u,units,cat,explicit,buckets,now))['unit']=='B'
    assert min(units,key=lambda u:sc.priority(u,units,cat,unknown,buckets,now))['unit']=='A'


def test_seller_selects_eligible_unit_and_records_stockout(tmp_path):
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,service_contract_v26_enabled=True)
    inv=j.snapshot('inventory.json');inv['units']=[food(now,'old',10),food(now,'fresh',60)]
    atomic_json(j.md/'inventory.json',inv)
    s=Seller.__new__(Seller);s.j=j;s.cfg=cfg
    u=s.select_unit('G111 Food must last at least 48h','t')
    assert u['unit']=='fresh' and u['service_contract']['minimum_hours']==48
    j.release('t');inv['units']=inv['units'][:1];atomic_json(j.md/'inventory.json',inv)
    assert s.select_unit('G111 Food must last at least 48h','unavailable') is None
    assert j.events('stockout')[0]['reason']=='shelf_life'


def seed_profile(j,now):
    for n in range(12):
        j.add('shop_opportunity',{'task':'task'+str(n),'sku':'G111'},ts=now-1000+n)
        if n<3:
            j.add('shop_settled',{'task':'task'+str(n),'sku':'G111','price':850,'cost':500},ts=now-500+n)
            sc.record(j,'task'+str(n),sc.parse('G111 Food must last at least 48h'),now=now-400+n*61)


def test_service_share_is_not_the_whole_sku_and_replenishes_eligible_depth(tmp_path):
    j,cfg,now=fixture(tmp_path);seed_profile(j,now)
    packet=sp.update(j,cfg,now);p=packet['shop_needs']['G111']
    assert p['need_fraction_scenario']==pytest.approx(.25)
    row=sp.need_plan(j,cfg,'G111',j.snapshot('catalog.json')['G111'],[food(now,str(i),10) for i in range(20)],
        12,20,1,3,now)
    assert row['needed']>0 and row['eligible_held']==0 and row['need_rate']==3
    assert row['eligible_draw_probability']==.25 and row['planning_scenario_not_guarantee']


def test_anonymous_burst_is_not_qualified_need(tmp_path):
    j,cfg,now=fixture(tmp_path)
    for n in range(100):sc.record(j,str(n),sc.parse('G111 Food must last at least 48h'),now=now-1)
    assert not sp.update(j,cfg,now)['shop_needs']


def test_supply_draw_observer_excludes_survivor_inventory(tmp_path):
    j,cfg,now=fixture(tmp_path);inv=j.snapshot('inventory.json')
    old=food(now,'old',10);old['supplied_at']=now-300
    inv['units']=[old,food(now,'fresh',60)];atomic_json(j.md/'inventory.json',inv)
    p=sp.update(j,cfg,now)
    assert len(p['draw_fractions'])==1 and len(j.events('shop_service_draw'))==1
    sp.update(j,cfg,now+61);assert len(j.events('shop_service_draw'))==1


def test_strict_probability_diminishes_and_restores_only_positive_book():
    strict={'eligible_draw_probability':.25,'eligible_held':0,'need_rate':3,'conditional_cost_ratio':.65}
    ps=[sp.strict_sale_probability(strict,n,1) for n in range(1,21)]
    assert 0<ps[-1]<ps[0]<=.25 and all(a>=b for a,b in zip(ps,ps[1:]))
    c={'evidence':{'arrival_rate_scenario':3,'planning_hours':6,'kind':'food','exploration':False,
        'scenario_unit_cost':500,'resale_price_scenario':950,'refund_stress':0,'list_price':1000}}
    old=marginal(c,1,100);c['evidence']['service_eligibility']=strict
    new=marginal(c,1,100)
    assert old['book']<new['book']<0 and new['probability']<=.25
    strict['eligible_draw_probability']=.75
    assert marginal(c,1,100)['book']>0
    c['evidence']['resale_price_scenario']=400;assert marginal(c,1,100)['book']<0


def test_service_packet_expires_and_prewindow_rejects_inventory_change(tmp_path):
    from r3 import prewindow
    j,cfg,now=fixture(tmp_path);seed_profile(j,now);sp.update(j,cfg,now)
    assert sp.context(j,now) and not sp.context(j,now+181)
    packet=prewindow.prepare(j,cfg,{'version':1},now);assert packet
    inv=j.snapshot('inventory.json');inv['restock']['open']=True;inv['units']=[food(now,'new',60)]
    assert prewindow.consume(j,cfg,{'version':1},inv,now+61) is None


def seed_route(j,now,oid,sku='G111',task='t',outcome='paid',price=900):
    j.add('buyer_counter_route',{'order':oid,'shop':'peer','sku':sku,'task':task,'ask':1000,'price':800,
        'terms_complete':True,'score_formula':'margin_share_v36','grade':'A','warranty_hours':6},ts=now-90)
    j.add('market_result',{'order':oid,'verified':outcome=='paid'},ts=now-50)
    if outcome=='paid':
        j.add('purchase',{'order':oid,'shop':'peer','sku':sku,'price':price},ts=now-50)
        j.add('agreement',{'order':oid,'shop':'peer','sku':sku,'price':price,'task':task},ts=now-55)
    elif outcome=='transport':j.add('attempt',{'order':oid,'shop':'peer','kind':'ReadTimeout'},ts=now-60)


def test_path_learning_does_not_credit_other_sku_or_task_and_excludes_transport(tmp_path):
    j,cfg,now=fixture(tmp_path);j.put('market_rules',{'sha256':'official','buyer_saved_formula':'margin_share_v36'})
    seed_route(j,now,'same');seed_route(j,now,'other_sku');seed_route(j,now,'other_task');seed_route(j,now,'transport',outcome='transport')
    j.add('purchase',{'order':'other_sku','shop':'peer','sku':'G222','price':700},ts=now-40)
    j.add('agreement',{'order':'other_task','shop':'peer','sku':'G111','price':900,'task':'different'},ts=now-40)
    # Remove ambiguity: latest purchase is same task's changed agreement for this case.
    j.add('purchase',{'order':'other_task','shop':'peer','sku':'G111','price':901},ts=now-39)
    row=np.update(j,cfg,now);model=row['model']
    assert row['samples']==3 and sum(r['paid'] for r in model['groups'].values())==1
    assert next(iter(model['groups'].values()))['final_price_ratios']==[.9]*3
    assert row['summary']['mode']=='shadow' and np.context(j,{'kind':'food'},'A',6,now) is None


def test_continuation_model_prices_final_outcome_and_discards_over_cap_samples():
    model={'id':'tested','kind':'goods','grade':'A','warranty_hours':6,'model':{'groups':{
        'goods|A|True|16':{'n':30,'paid':20,'probability':.6,'final_price_ratios':[.85,.9,1.1],'seconds':8}}}}
    scenario=np.scenario(model,800,1000,1000)
    assert scenario['prices']==[850,900] and scenario['probability']==pytest.approx(.4)
    state={'metric':'saved','score_formula':'margin_share_v36','unit_cost':600,'saved':.5,'deals':100}
    actions=buyer_price_actions(1000,1000,{'list_price':1200},1000,.8,0,3,saved_state=state,path_model=model)
    c=next(a for a in actions if a['price']==800)
    assert c['payment_price_scenarios']==[850,900] and c['path_expected_seconds']==8
    assert c['completion_probability']==pytest.approx(.4)
    from r3.buyer_scoring import terms_delta
    expected=sum(terms_delta(state,p,1200,0,0,.4)['saved'] for p in (850,900))/2
    assert c['expected_delta']['saved']==pytest.approx(expected)


def test_temporal_gate_rejects_uninformative_models_and_accepts_predictive_fixture():
    bad=[dict(kind='goods',grade='A',warranty_hours=6,ask=1000,price=800,paid=i%2==0,
        final_ratio=.9 if i%2==0 else None,seconds=8) for i in range(100)]
    assert np.evaluate(bad)['mode']=='shadow'
    good=[dict(kind='goods',grade='A',warranty_hours=6,ask=1000,price=800 if i%2 else 990,
        paid=i%2==0,final_ratio=.995 if i%2==0 else None,seconds=8) for i in range(100)]
    assert np.evaluate(good)['mode']=='validated_predictive_scenario'


def task(terms):return {'id':'t','status':{'state':'TASK_STATE_INPUT_REQUIRED','message':{'parts':[{'kind':'data','data':terms}]}}}


def test_confirmation_is_once_bounded_and_original_quote_survives_timeout(tmp_path):
    j,cfg,now=fixture(tmp_path);j.put('market_rules',{'buyer_metric':'saved','buyer_saved_formula':'margin_share_v36'})
    order={'order':'o','kind':'goods','budget':1000};c={'shop':'peer','sku':'G111','product':{'kind':'goods','list_price':1000},'reference_price':900}
    original=task({'ask':900,'sku':'G111'});calls=[]
    class Peer:
        deadline=now+30
        def send(self,text,tid):
            calls.append((text,self.deadline));raise TimeoutError()
    p=Peer();assert clarify(p,original,c,order,j,cfg,now+30) is original
    assert len(calls)==1 and calls[0][1]-time.time()<=2 and 'actual priced warranty' in calls[0][0]
    assert p.deadline==now+30 and j.events('buyer_terms_confirmation')[0]['status']=='unconfirmed'
    assert clarify(p,original,c,order,j,cfg,now+30) is original and len(calls)==1


def test_complete_contract_needs_no_extra_round_trip(tmp_path):
    j,cfg,now=fixture(tmp_path);original=task({'ask':900,'grade':'A','warranty_hours':6})
    p=SimpleNamespace(send=lambda *a:pytest.fail('complete quote must not be re-queried'))
    assert clarify(p,original,{'shop':'peer','sku':'G111'}, {'kind':'goods'},j,cfg,now+30) is original


def test_bid_frontier_has_near_ask_and_saved_targets_without_creating_a_spending_cap(tmp_path):
    j,cfg,now=fixture(tmp_path);j.put('market_rules',{'buyer_metric':'saved','buyer_saved_formula':'margin_share_v36'})
    atomic_json(j.md/'board.json',{'buyers':[{'name':'seochanit','saved':.55,'deals':100},{'name':'other','saved':.57,'deals':110}]})
    p={'kind':'goods','list_price':1000};o={'budget':1000};a=buyer_assessment(p,o,{'grade':'A','warranty_hours':6},j,cfg)
    assert min(a['bid_targets'],key=lambda x:abs(x-.57001))==pytest.approx(.57001)
    assert not a['saved_target_is_hard_cap'] and a['cap']==1000
    prices=buyer_price_actions(900,1000,p,900,.92,0,4,saved_state=a['saved_state'],path_model=a['negotiation_path_model'],target_saved=a['bid_targets'])
    assert {873,891,900}<={r['price'] for r in prices}


def test_configuration_preserves_cost_routing_and_unrestricted_optional_capital():
    cfg=policy();assert cfg['service_contract_v26_enabled'] and cfg['daily_api_budget_usd'] is None
    assert not cfg['shop_inventory_caps_enabled'] and not cfg['shop_internal_capital_caps_enabled']
    assert cfg['background_llm_roles']['market_research_analyst']['model']=='gpt-6-luna'
    assert cfg['background_llm_roles']['market_research_critic']['model']=='gpt-6.1-terra'
    assert not any('astra' in v['model'] for v in cfg['background_llm_roles'].values())


@pytest.mark.parametrize('fast',[True,False])
def test_both_rfq_paths_use_margin_share_instead_of_list_discount(tmp_path,monkeypatch,fast):
    from r3 import quote_market as qm
    j,cfg,now=fixture(tmp_path);cfg=dict(cfg,buyer_throughput_enabled=fast,system_framework_enabled=False,
        buyer_fast_accept_margin=10,buyer_patch34_enabled=True)
    def assessment(product,order,terms,*args,**kwargs):
        return {'cap':1000,'failure':0,'covered_failure':0,'conditional_terms':[],
            'saved_state':{'metric':'saved','score_formula':'margin_share_v36','saved':.5,'deals':100,
                'unit_cost':700 if terms['grade']=='A' else 250},'required_grade':terms['grade'],'required_warranty':0}
    monkeypatch.setattr(qm,'buyer_assessment',assessment)
    class Peer:
        def discover(self,s):self.shop=s
        def send(self,text,*args):return task({'ask':600 if self.shop=='A' else 400,'grade':self.shop,'warranty_hours':0,'sku':'G111'})
        def close(self):pass
        def cancel(self,*a):pass
    rows=[{'id':s,'shop':s,'sku':'G111','score':0,'evidence':{},'product':{'kind':'goods','list_price':1000}} for s in ('A','C')]
    prepared,contacted=qm.compare_quotes({'order':'o','kind':'goods','budget':1000},rows,
        {s:{'pubkey':'synthetic'} for s in ('A','C')},j,cfg,Peer,now+30)
    assert contacted==2 and {r['shop']:r['score'] for r in rows}==pytest.approx({'A':1,'C':.8})
    qm.close_quotes(prepared)


def test_confirmed_quote_uses_actual_new_price_and_same_task(tmp_path):
    from r3.protocol import quote,quote_terms
    j,cfg,now=fixture(tmp_path);c={'shop':'peer','sku':'G111','product':{'kind':'goods','list_price':1000},'reference_price':800}
    original=task({'ask':800,'sku':'G111'})
    p=SimpleNamespace(deadline=now+30,send=lambda text,tid:task({'ask':850,'sku':'G111','grade':'B','warranty_hours':6}))
    actual=clarify(p,original,c,{'order':'o','kind':'goods','budget':1000},j,cfg,now+30)
    assert actual['id']==original['id'] and quote(actual)==850 and quote_terms(actual)['grade']=='B'
    assert p.deadline==now+30


def test_seller_followup_replaces_unit_and_preserves_new_cost_accounting(tmp_path,monkeypatch):
    import asyncio
    import seller_r3 as sr
    j,cfg,now=fixture(tmp_path);inv=j.snapshot('inventory.json')
    old,fresh=food(now,'old',10),food(now,'fresh',60);fresh['cost']=650
    inv['units']=[old,fresh];atomic_json(j.md/'inventory.json',inv)
    messages=[]
    class Updater:
        def __init__(self,**kwargs):pass
        def new_agent_message(self,**kwargs):return SimpleNamespace(**kwargs)
        async def reject(self,*args):messages.append('rejected')
        async def update_status(self,*args,**kwargs):messages.append(kwargs.get('metadata'))
    # A deterministic quote avoids any API/key requirement and real signing.
    monkeypatch.setattr(sr,'TaskUpdater',Updater)
    monkeypatch.setattr(sr,'get_message_text',lambda message:message.text)
    monkeypatch.setattr(sr,'prepare_quote',lambda *a:({},[{'id':'q','action':'counter','price':850,'warranty_hours':0,'expected_delta':{'book':200}}]))
    s=Seller.__new__(Seller);s.j=j;s.cfg=cfg;s.deals={};s.chooser=SimpleNamespace(choose=lambda role,cs,*a,**k:cs[0])
    spec=sc.parse('G111 Food must last at least 4h')
    s.deals['t']={'unit':dict(old,requested_use_hours=4),'ask':850,'turns':1,'until':now+180,
        'config':cfg,'strategy':{'version':1},'service_contract':spec}
    j.reserve('old','t',180,now)
    context=SimpleNamespace(message=SimpleNamespace(text='Food must last at least 48h',message_id='m',metadata={}))
    asyncio.run(s._execute(SimpleNamespace(id='t',context_id='c'),context,None))
    assert s.deals['t']['unit']['unit']=='fresh' and j.get('shop_strategy:t')['cost']==650
    assert messages[-1]['unit']=='fresh'
    with j.connect() as db:assert db.execute('SELECT unit FROM reservations WHERE task=?',('t',)).fetchall()==[('fresh',)]
