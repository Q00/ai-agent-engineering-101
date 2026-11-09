"""Regression cases from live price-only counters; no external trading."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from r3.protocol import quote_terms, retain_quote_terms


def test_actual_full_refund_phrase_is_a_declared_promise():
    from test_buyer_v11 import task
    assert quote_terms(task(text='grade B, 6 h full-refund warranty. Asking 1766555.'))['warranty_hours'] == 6
    assert quote_terms(task({'warranty_hours': 0}, text='6 h full-refund warranty'))['warranty_hours'] == 0


def test_price_only_reply_retains_grade_and_explicit_zero_warranty():
    first = retain_quote_terms({}, {'sku':'G123abc','grade':'C','warranty_hours':0,'ask':7000}, 100)
    second = retain_quote_terms(first, {'ask':6500}, 110)
    assert second['grade'] == 'C' and second['warranty_hours'] == 0
    assert first['ask'] == 7000 and second['ask'] == 6500


def test_relative_shelf_life_ages_instead_of_refreshing_on_counters():
    first = retain_quote_terms({}, {'unit':'u','hours_left':2}, 100)
    assert retain_quote_terms(first, {'ask':50}, 500)['expires_at'] == 7300
    assert retain_quote_terms(first, {'unit':'u','hours_left':2}, 500)['expires_at'] == 7300
    assert retain_quote_terms(first, {'unit':'v','hours_left':2}, 500)['expires_at'] == 7700


@pytest.mark.parametrize('old,new',[
    ({'sku':'G123abc'}, {'sku':'G456def'}),
    ({'unit':'u','grade':'C'}, {'unit':'v'}),
    ({'unit':'u','expires_at':100}, {'unit':'v'}),
])
def test_changed_unit_or_sku_cannot_inherit_unverified_quality(old,new):
    with pytest.raises(ValueError):retain_quote_terms(old,new,100)


def test_new_unit_fresh_grade_and_expiry_replace_old_unit_claims():
    merged = retain_quote_terms({'unit':'u','grade':'C','cert':True,'expires_at':200,'warranty_hours':3},
                                {'unit':'v','grade':'A','expires_at':900},100)
    assert merged['grade'] == 'A' and merged['expires_at'] == 900
    assert 'cert' not in merged and merged['warranty_hours'] == 3


def test_execute_keeps_c_grade_and_zero_warranty_through_price_only_counter(tmp_path,monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from test_buyer_v11 import setup, task, cfg
    from test_market_contract import receipt, pub
    from r3.state import Journal
    import market_buyer
    j=Journal(tmp_path); now=time.time(); product=setup(j,now)
    key=Ed25519PrivateKey.generate()
    rec=dict(receipt(key,sku='G123abc',price=1000),grade='C',warranty_hours=0,cert=True)
    observed=[]; original=market_buyer.buyer_assessment
    def assess(product,order,terms,*args,**kwargs):
        observed.append((terms.get('grade'),terms.get('warranty_hours')))
        return original(product,order,terms,*args,**kwargs)
    monkeypatch.setattr(market_buyer,'buyer_assessment',assess)
    class Peer:
        calls=0
        def send(self,*args):
            self.calls+=1
            if self.calls==1:return task(text='Counteroffer: 6000.')
            return {'id':'t','status':{'state':'TASK_STATE_COMPLETED'},'artifacts':[{'parts':[{'data':rec}]}]}
        def close(self):pass
        def cancel(self,*args):pass
    class Choose:
        def choose(self,role,rows,*args,**kwargs):return next((r for r in rows if r['id']=='counter'),rows[0])
    candidate={'id':'c','shop':'https://shop.example','sku':'G123abc','product':product,'reference_price':6000}
    initial=task({'grade':'C','warranty_hours':0,'ask':6000,'sku':'G123abc'})
    peer=Peer()
    out=market_buyer.execute({'order':'o','kind':'goods','budget':10000,'acceptable':['G123abc'],'expires':now+120},
        j,Choose(),Peer,cfg(),{'version':1},{candidate['shop']:{'pubkey':pub(key)}},[candidate],
        now+30,{'c':(peer,initial)},1,{'receipt':None})
    assert peer.calls==2 and observed[:2]==[('C',0),('C',0)]
    assert out['receipt'] and out['accept']['grades']==['C'] and out['accept']['warranty_hours']==0
