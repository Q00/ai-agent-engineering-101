import time
from r3.state import Journal,atomic_json
from r3.long_memory import refresh,contract_summary,observe_reviews
from r3.customer_memory import retain


def test_contract_summary_survives_working_visit_eviction_and_separates_terms(tmp_path):
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'catalog.json',{'G1':{'list_price':1000}})
    for i in range(4):
        j.add('shop_settled',{'task':str(i),'unit':str(i),'sku':'G1','category':'bed','grade':'A',
            'warranty_hours':6,'price':600+i,'cost':500},ts=now-100)
    j.add('customer_visit',{'task':'old','category':'bed','kind':'goods'})
    j.add('customer_paid',{'task':'old','price':650,'sku':'G1'})
    j.add('customer_visit',{'task':'new','category':'bed','kind':'goods'})
    retain(j,max_visits=1)
    assert [r['task'] for r in j.events('customer_visit')]==['new']
    with j.connect() as c:assert c.execute('SELECT count(*) FROM memory_episodes WHERE id=?',('visit:old',)).fetchone()[0]==1
    refresh(j,now)
    assert contract_summary(j,{'category':'bed','grade':'A'},6,now)['samples']==4
    assert contract_summary(j,{'category':'bed','grade':'C'},6,now)['samples']==0
    assert contract_summary(j,{'category':'bed','grade':'A'},0,now)['samples']==0
    refresh(j,now+121)
    with j.connect() as c:assert c.execute("SELECT count(*) FROM memory_episodes WHERE kind='shop_sale'").fetchone()[0]==4


def test_reviews_are_deduplicated_without_guessing_authors(tmp_path):
    j=Journal(tmp_path);shops=[{'name':'seochanit','reviews':[{'stars':2,'review':'grade A; ledger #123.'}]}]
    observe_reviews(j,shops);observe_reviews(j,shops)
    rows=j.events('shop_review_observed');assert len(rows)==1
    assert rows[0]['ledger']==123 and not rows[0]['identity_known']


def test_unidentified_reviews_never_manufacture_a_rating_forecast(tmp_path):
    from r3.long_memory import rating_summary
    j=Journal(tmp_path);now=time.time()
    j.add('shop_settled',{'task':'t','sku':'G1','grade':'A','warranty_hours':6,'price':100,'category':'bed'},eid='settlement:123',ts=now-100)
    observe_reviews(j,[{'name':'seochanit','reviews':[{'stars':2,'review':'ledger #123.'}]}],now-1)
    refresh(j,now)
    summary=rating_summary(j,{'grade':'A'},6)
    assert summary['reviews']==1 and summary['account_balanced_stars'] is None
    assert not summary['official_all_delta_available']


def test_verified_ledger_result_links_only_our_sale_and_real_owner(tmp_path):
    from r3.long_memory import observe_market_result
    j=Journal(tmp_path);now=time.time()
    atomic_json(j.md/'board.json',{'buyers':[{'name':'buyer','owner':{'login':'real-account'}}]})
    event={'seq':900,'ts':now,'round':3,'data':{'verified':True,'checks':{'settled':True},
        'buyer':'buyer','receipt':{'shop':'Seochanit Shop','task':'t'},'stars':2,'review':'ledger #123.'}}
    assert not observe_market_result(j,event)
    j.add('shop_settled',{'task':'t'},eid='settlement:123')
    assert observe_market_result(j,event)
    row=j.events('shop_review_observed')[0]
    assert row['identity_known'] and row['author']=='owner:real-account' and row['ledger']==123
    assert not observe_market_result(j,event)
