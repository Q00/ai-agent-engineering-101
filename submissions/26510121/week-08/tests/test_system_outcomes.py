import time
from r3.state import Journal
from r3.outcomes import reconcile,latest


def purchase(j,oid,unit,now):
    p={'order':oid,'unit':unit,'name':'same','kind':'goods','paid_at':now-25000,
       'price':100,'value':200,'warranty_hours':6,'grade':'A'}
    j.add('purchase',p);return p


def test_exact_units_resolve_same_name_and_confirm_refunds(tmp_path):
    j=Journal(tmp_path);now=time.time();ps=[purchase(j,'a','u1',now),purchase(j,'b','u2',now)]
    j.add('broken_news',{'name':'same','broke_at':now-24000})
    j.add('public_broken',{'order':'a','unit':'u1','hours_after_sale':1,'refunded':100},ts=now-1)
    j.put('public_feed_caught_up_at',now)
    reconcile(j,ps,now);out=latest(j)
    assert out['a']['broken'] and out['a']['refund_status']=='confirmed' and out['a']['surplus']==0
    assert not out['b']['broken'] and out['b']['matured']
    count=len(j.events('outcome'));reconcile(j,ps,now+1);assert len(j.events('outcome'))==count


def test_late_exact_break_overrides_healthy_without_rewriting(tmp_path):
    j=Journal(tmp_path);now=time.time();p=purchase(j,'a','u',now)
    j.put('public_feed_caught_up_at',now);reconcile(j,[p],now)
    assert not latest(j)['a']['broken']
    j.add('public_broken',{'order':'a','unit':'u','hours_after_sale':2,'refunded':0})
    reconcile(j,[p],now+31)
    assert latest(j)['a']['broken'] and latest(j)['a']['saved_contribution']==-1
    assert len(j.events('outcome'))==2


def test_stale_feed_does_not_clear_ambiguity_or_invent_health(tmp_path):
    j=Journal(tmp_path);now=time.time();p=purchase(j,'a','u',now)
    j.put('public_feed_caught_up_at',now-500)
    assert reconcile(j,[p],now)==0


def test_unconfirmed_refund_stays_pending_until_market_fact(tmp_path):
    j=Journal(tmp_path);now=time.time();p=purchase(j,'a','u',now)
    j.add('public_broken',{'order':'a','unit':'u','hours_after_sale':2},ts=now-1)
    reconcile(j,[p],now);assert latest(j)['a']['refund_status']=='expected_by_warranty_rule'
    j.add('public_refund',{'order':'a','unit':'u','amount':100})
    reconcile(j,[p],now+31);assert latest(j)['a']['refund_status']=='confirmed'
