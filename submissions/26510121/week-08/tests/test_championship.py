"""Regressions for the observed cash/call constraint and eligibility failures."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from r3.state import Journal,atomic_json,policy
from r3.shop_growth import allocate_championship,allocate_velocity,inventory_limit
from r3.risk import shop_refund_risk
from r3.buyer_scoring import remember_refusal,route_availability,offer_ineligible
from test_shop_velocity import candidate


def cfg(**patch):
    return dict(policy(),**dict(shop_inventory_caps_enabled=True,shop_championship_enabled=True,buyer_championship_enabled=True,**patch))


def test_call_slot_prefers_absolute_growth_when_cash_is_abundant():
    cheap=candidate('cheap',10000,5000,rate=8,qty=12)
    goods=candidate('goods',1200000,100000,rate=1,qty=1)
    config=cfg(restock_max_lines=1)
    old=allocate_velocity([cheap,goods],{}, {'spendable':3000000},config,[],{})
    new=allocate_championship([cheap,goods],{}, {'spendable':3000000},config,[],{})
    assert old[0]['sku']=='cheap'
    assert new[0]['sku']=='goods'
    assert new[0]['capital_commitment']<=3000000


def test_capital_constrained_portfolio_still_funds_affordable_goods():
    rows=allocate_championship([candidate('small',10000,3000,rate=8,qty=12),
                               candidate('large',1200000,100000,rate=1,qty=1)],
                              {},{'spendable':100000},cfg(),[],{})
    assert rows and all(r['sku']=='small' for r in rows)
    assert sum(r['capital_commitment'] for r in rows)<=100000


def test_new_portfolio_keeps_cash_category_exploration_and_legal_lot_caps():
    inputs=[candidate(str(n),10000,3000,rate=5,qty=12,
                      category='same' if n<5 else str(n),explore=n>=8) for n in range(12)]
    rows=allocate_championship(inputs,{}, {'spendable':300000},cfg(restock_max_lines=4),[],{})
    assert len(rows)<=4
    assert sum(r['capital_commitment'] for r in rows)<=300000
    assert sum(r['capital_commitment'] for r in rows if r['category']=='same')<=180000
    assert sum(r['capital_commitment'] for r in rows if r['evidence']['exploration'])<=30000
    assert all(1<=r['qty']<=12 for r in rows)


def test_sixty_cheap_units_do_not_block_a_profitable_uncovered_sku():
    existing=[{'sku':'old'} for _ in range(60)]
    rows=allocate_championship([candidate('new',100000,20000,rate=3,qty=2)],
                              {},{'spendable':1000000},cfg(),existing,{})
    assert rows and sum(r['qty'] for r in rows)+len(existing)<=inventory_limit(cfg())
    capped=[{'sku':'old'} for _ in range(inventory_limit(cfg()))]
    assert not allocate_championship([candidate('new',100000,20000)],{},
                                    {'spendable':1000000},cfg(),capped,{})


def test_seller_risk_uses_goods_only_mature_units_and_partial_cover(tmp_path):
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'catalog.json',{'G':{'kind':'goods'},'F':{'kind':'food'}})
    for n in range(100):
        j.add('shop_settled',{'sku':'G','unit':str(n),'grade':'A','paid_at':now-25000},ts=now-25000)
    for n in range(200):
        j.add('shop_settled',{'sku':'F','unit':'f'+str(n),'grade':'A','paid_at':now-25000},ts=now-25000)
    j.add('shop_settled',{'sku':'G','unit':'pending','grade':'A','paid_at':now-10},ts=now-10)
    for n,hours in [(0,1),(1,5),(2,5.9)]:
        j.add('public_broken',{'unit':str(n),'hours_after_sale':hours,'refunded':0},ts=now-100)
    short=shop_refund_risk(j,'A',3,cfg(),now);full=shop_refund_risk(j,'A',6,cfg(),now)
    assert short['mature_samples']==full['mature_samples']==100
    assert short['covered_breaks']==1 and full['covered_breaks']==3
    assert 0<short['refund_upper']<full['refund_upper']<.3
    assert shop_refund_risk(j,'A',0,cfg(),now)['refund_upper']==0


def test_unknown_break_time_cannot_be_counted_as_outside_warranty(tmp_path):
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'catalog.json',{'G':{'kind':'goods'}})
    for n in range(20):
        j.add('shop_settled',{'sku':'G','unit':str(n),'grade':'B','paid_at':now-25000},ts=now-25000)
    j.add('public_broken',{'unit':'0'},ts=now-100)
    assert shop_refund_risk(j,'B',3,cfg(),now)['covered_breaks']==1


def test_routing_refusal_is_need_scoped_soft_and_expires(tmp_path):
    j=Journal(tmp_path);now=time.time()
    remember_refusal(j,{},'shop',{'unit':'u','item':'G'},
                     ['not paying: 12.0 h of shelf life left, the policy asks for 24'],now)
    assert route_availability(j,'shop','G',{'use_within_hours':24},now)['factor']<1
    assert route_availability(j,'shop','G',{'use_within_hours':1},now)['factor']==1
    assert route_availability(j,'other','G',{'use_within_hours':24},now)['factor']==1
    assert route_availability(j,'shop','G',{'use_within_hours':24},now+3601)['factor']==1


def test_explicit_food_terms_fail_before_negotiation_and_final_receipt():
    now=time.time();order={'kind':'food','use_within_hours':24}
    assert offer_ineligible({'expires_at':now+12*3600},order,cfg(),now)=='shelf_life'
    assert offer_ineligible({'expires_at':now+25*3600},order,cfg(),now) is None
    assert offer_ineligible({},order,cfg(),now) is None  # final certificate still required


def test_real_acceptable_basket_can_support_a_referenced_alternative_tip(tmp_path):
    from r3.buyer_insights import alternatives
    j=Journal(tmp_path);now=time.time()
    catalog={s:{'kind':'food','category':'snack','name':s} for s in ['G1','G2','outside']}
    j.add('buyer_order',{'acceptable':['G1','G2']},ts=now-20)
    book={s:{'term_prices':[{'grade':'B','warranty_hours':0,'n':20,'median':price,'refs':[seq]}]}
          for s,price,seq in [('G1',500,11),('G2',900,12),('outside',100,13)]}
    drafts=list(alternatives(j,book,catalog,now))
    assert drafts and drafts[0][2]==[11,12]
    assert 'outside' not in drafts[0][1]
    book['G2']['term_prices'][0]['grade']='A'
    assert not list(alternatives(j,book,catalog,now))


def test_same_sku_price_posts_are_spaced_even_with_new_receipts(tmp_path):
    from r3.buyer_forum import schedule
    j=Journal(tmp_path);now=time.time()
    catalog={'G':{'name':'snack','list_price':1000,'kind':'food'}}
    book={'G':{'term_prices':[{'grade':'B','warranty_hours':0,'n':8,'median':700,'refs':[12,13]}]}}
    for n in range(2):
        j.add('purchase',{'order':str(n),'sku':'G','kind':'food','value':1000,'price':600,
                          'grade':'B','ledger':11+n,'paid_at':now-20-n},ts=now-20-n)
    schedule(j,cfg(),book,catalog,now)
    schedule(j,cfg(),book,catalog,now+301)
    assert len(j.pending_posts())==1


def test_monitor_matches_peer_rates_and_ignores_unscored_buyers(tmp_path):
    from r3.progress import observe
    j=Journal(tmp_path);now=time.time()
    def board(book,saved):
        return {'round':3,'shops':[{'name':'seochanit','book':book,'all':.5},
                                  {'name':'peer','book':100000,'all':.8}],
                'buyers':[{'name':'seochanit','saved':saved,'deals':10,'all':.5},
                          {'name':'peer','saved':.4,'deals':10,'all':.8},
                          {'name':'unscored','saved':0,'deals':0}]}
    observe(j,board(1000,.2),{'balance':10000,'units':[]},{'spendable':8000},now-3600)
    out=observe(j,board(2000,.3),{'balance':10000,'units':[]},{'spendable':8000},now)
    assert out['pace']['shop']['own_per_hour']==1000
    assert out['pace']['buyer']['own_per_hour']==pytest.approx(.1)
    assert out['ranks']['buyer']['population']==2
    assert out['idle_spendable_fraction']==.8


def test_quote_learning_does_not_mix_grade_and_warranty_cohorts(tmp_path):
    from r3.meta import quote_completion
    j=Journal(tmp_path);now=time.time()
    for n,(grade,warranty) in enumerate([('A',0),('A',6),('B',0)]):
        j.add('shop_quote',{'task':str(n),'sku':'G','price':1000,'grade':grade,
                            'warranty_hours':warranty},ts=now-600)
        if n==0:j.add('shop_settled',{'task':str(n)},ts=now-500)
    result=quote_completion(j,'G',1000,now,grade='A',warranty=0)
    assert result['samples']==1 and result['paid']==1


def test_food_failure_from_a_different_use_window_does_not_poison_routing(tmp_path):
    from r3.economy import buyer_candidates
    j=Journal(tmp_path);now=time.time()
    catalog={'G':{'kind':'food','category':'snack','list_price':1000,'shelf_hours':48}}
    for n in range(10):
        j.add('attempt',{'order':str(n),'shop':'shop','sku':'G','kind':'no_agreement',
                         'success':False,'use_within_hours':48},ts=now-10-n)
    shops=[{'name':'peer','url':'shop','offers':[{'sku':'G','qty':1}]}]
    c=buyer_candidates({'acceptable':['G'],'budget':1000,'use_within_hours':1},shops,catalog,j,cfg())[0]
    assert c['evidence']['peer_failures']==0


def test_rfq_drops_known_expired_terms_before_offer_selection(tmp_path):
    from r3.quote_market import compare_quotes,close_quotes
    j=Journal(tmp_path);now=time.time();closed=[]
    class Peer:
        deadline=None
        def discover(self,url):pass
        def send(self,text):
            return {'id':'t','status':{'state':'TASK_STATE_INPUT_REQUIRED'},
                    'metadata':{'ask':600,'sku':'G','grade':'B','expires_at':now+3600}}
        def close(self):closed.append(True)
        def cancel(self,task):pass
    candidates=[{'id':str(n),'shop':str(n),'sku':'G','score':.4,'evidence':{},
                 'product':{'list_price':1000,'kind':'food'}} for n in range(2)]
    prepared,_=compare_quotes({'order':'o','kind':'food','budget':1000,'use_within_hours':24},
                              candidates,{str(n):{'pubkey':'registered'} for n in range(2)},
                              j,cfg(),Peer,now+18)
    assert not candidates and len(j.events('buyer_declined_terms'))==2
    close_quotes(prepared)
    assert len(closed)==2
