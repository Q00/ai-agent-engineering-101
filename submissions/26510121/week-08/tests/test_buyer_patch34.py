"""Patch 3.4 financial, quality, identity and publication boundaries."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import httpx
from r3.state import Journal,policy,atomic_json
from r3.buyer_scoring import contribution,delta,state,terms_delta,saved_price_cap,remember_refusal,blocked_units


def fixture(tmp_path):
    j=Journal(tmp_path);cfg=dict(policy(),buyer_patch34_enabled=True)
    atomic_json(j.md/'board.json',{'round':3,'buyers':[
        {'name':'seochanit','saved':.25,'surplus':100000,'deals':100,'forum':.5,'posts':2},
        {'name':'peer','saved':.4,'surplus':500000,'deals':200,'forum':.6,'posts':3}]})
    j.put('market_rules',{'buyer_metric':'saved','buyer_compatible':True})
    return j,cfg


def test_same_discount_scores_equally_for_snack_and_sofa(tmp_path):
    j,cfg=fixture(tmp_path);s=state(j,cfg)
    a=terms_delta(s,700,1000);b=terms_delta(s,700000,1000000)
    assert a['saved']==b['saved'] and b['surplus']==1000*a['surplus']
    assert delta(s,.30)==pytest.approx((.30-.25)/201)
    assert delta(s,.20)<0


def test_unserved_penalty_is_minus_one_and_full_refund_zero():
    assert contribution(200,1000,1,0)==-1
    assert contribution(900,1000,1,1)==0
    assert contribution(700,1000,.2,.2)==pytest.approx(.24)
    assert contribution(700,1000,.2,0)==pytest.approx(.04)


def test_saved_cap_accounts_for_refund_quality_and_existing_average(tmp_path):
    j,cfg=fixture(tmp_path);s=state(j,cfg)
    uninsured,target=saved_price_cap(10000,s,.2,0)
    insured,_=saved_price_cap(10000,s,.2,.2)
    assert 0<uninsured<insured<7500
    assert contribution(uninsured,10000,.2,0)>=target
    assert contribution(insured,10000,.2,.2)>=target
    assert saved_price_cap(10000,s,1,0)[0]==0


def test_new_objective_chooses_percentage_instead_of_large_absolute_money(tmp_path,monkeypatch):
    from r3.model import Chooser
    j,cfg=fixture(tmp_path);s=state(j,cfg)
    candidates=[{'id':'sofa','score':.3,'expected_delta':terms_delta(s,700000,1000000)},
                {'id':'snack','score':.4,'expected_delta':terms_delta(s,600,1000)}]
    chooser=Chooser(j,cfg)
    monkeypatch.setattr(chooser,'_call',lambda *a,**k:pytest.fail('numeric Saved decisions need no API wait'))
    assert chooser.choose('buyer',candidates,{'buyer_execution':True,'operation':'price'})['id']=='snack'
    assert j.events('buyer_saved_execution')
    assert 'saved' in j.events('prediction')[0]['context']['next_rank_thresholds']


def test_class_cohort_uses_one_selected_buyer_per_student(tmp_path):
    from r3.meta import eligible_rows
    board={'round':3,'buyer_rank_scope':'class','players':[
        {'student':True,'buyer':{'name':'seochanit'}},
        {'student':True,'buyer':{'name':'peer'}},{'student':True,'buyer':None}],
        'buyers':[{'name':n,'deals':10} for n in ['seochanit','peer','outside','other-alias']]}
    assert {r['name'] for r in eligible_rows(board,'buyer','seochanit')}=={'seochanit','peer'}


def test_refused_food_unit_can_still_serve_a_shorter_requirement(tmp_path):
    j,cfg=fixture(tmp_path);now=time.time()
    rec={'unit':'u','item':'G1'}
    remember_refusal(j,{},'shop',rec,['not paying: 12.0 h of shelf life left, the policy asks for 24'],now)
    assert blocked_units(j,'shop','G1',{'use_within_hours':24},now)==['u']
    assert blocked_units(j,'shop','G1',{'use_within_hours':1},now)==[]
    remember_refusal(j,{},'shop',{'unit':'u2','item':'G1'},['payment failed: ReadTimeout'],now)
    assert len(j.get('buyer_offer_blocks'))==1


def test_stockout_memory_expires_and_never_creates_a_second_payment(tmp_path):
    j,cfg=fixture(tmp_path);now=time.time()
    remember_refusal(j,{},'shop',{'unit':'u','item':'G1'},['checkout refused: unit u is not in stock for this order'],now)
    assert blocked_units(j,'shop','G1',{},now)==['u']
    assert blocked_units(j,'shop','G1',{},now+121)==[]


def test_peer_deadline_stops_before_network_and_bounds_each_request():
    from r3.protocol import Peer
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'supportedInterfaces':[{'protocolBinding':'JSONRPC','protocolVersion':'1.0','url':'/rpc'}]})
    p=Peer(transport=httpx.MockTransport(handle));p.deadline=time.time()-1
    with pytest.raises(TimeoutError):p.discover('https://peer.example')
    assert not calls
    p.deadline=time.time()+1;p.discover('https://peer.example')
    assert 0<calls[0].extensions['timeout']['read']<=1
    p.close()


def test_buyer_revision_preserves_shop_and_quality_records(tmp_path):
    from r3.adaptive import activate_buyer_revision
    j,cfg=fixture(tmp_path)
    j.put('adaptive:buyer',{'version':6,'patch':{},'status':'stable'})
    j.put('adaptive:shop',{'version':7,'patch':{},'status':'stable'})
    j.put('quality_risk_v4',{'grades':{'A':{'n':100}}})
    activate_buyer_revision(j,'buyer-v8-test',100)
    assert j.get('adaptive:buyer')['version']==7
    assert j.get('adaptive:shop')['version']==7
    assert j.get('quality_risk_v4')['grades']['A']['n']==100
    activate_buyer_revision(j,'buyer-v8-test',200)
    assert j.get('buyer_revision_epoch')==100


def test_new_insight_is_same_terms_referenced_and_not_repeated(tmp_path):
    from r3.buyer_forum import schedule
    j,cfg=fixture(tmp_path);now=time.time()
    cat={'G1':{'name':'snack','kind':'food','list_price':1000}}
    j.add('purchase',{'order':'o','sku':'G1','kind':'food','value':1000,'price':600,
                      'grade':'B','ledger':11,'paid_at':now-20},ts=now-20)
    book={'G1':{'term_prices':[{'grade':'B','warranty_hours':0,'n':8,'median':700,'refs':[12,13]}]}}
    schedule(j,cfg,book,cat,now)
    queued=j.pending_posts();assert len(queued)==1
    payload=queued[0][1]
    assert payload['refs']==[11,12,13] and '40.0%' in payload['post']
    schedule(j,cfg,book,cat,now+301)
    assert len(j.pending_posts())==1
    assert all('vote' not in p for _,p in queued)


def test_buyer_uses_legal_five_minute_cadence_without_changing_shop(tmp_path):
    from r3.forum import deliver
    j,cfg=fixture(tmp_path);now=time.time()
    class Market:
        token={'buyer':'synthetic','shop':'synthetic'}
        def __init__(self):self.calls=[]
        def call(self,method,path,role,**kw):
            self.calls.append(role)
            return type('Response',(),{'status_code':201})()
    m=Market()
    j.put('forum_last_sent:buyer',now-301);j.put('forum_last_sent:shop',now-301)
    j.queue_post({'post':'new evidence','as':'buyer','refs':[1]},'buyer-insight:1')
    j.queue_post({'post':'shop stock','as':'shop'},'shop:1')
    j.queue_post({'post':'superseded','as':'buyer'},'paid-observation:old')
    deliver(m,j,cfg,now)
    assert m.calls==['buyer']
    assert j.events('forum_queue_retired')[0]['count']==1


def test_rule_audit_understands_buyer_change_separately(tmp_path):
    from r3.control import audit_rules
    j,cfg=fixture(tmp_path)
    r=audit_rules(j,'shops by book, revenue and stars; each further unit adds 0.2%; up to +50%; '
                  'buyers by saved rate; divided by deals + 100; at least 50 settled deals; one stance per; next_dislike_fee')
    assert r['compatible'] and r['buyer_metric']=='saved' and r['forum_vote_min_deals']==50
    assert audit_rules(j,'unrecognized buyer scoring')['buyer_compatible'] is False
