"""Stale heartbeat is recovered only with three current public confirmations."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from r3.state import Journal,atomic_json,read_json
from r3.transport_health import refresh


def fixture(tmp_path,monkeypatch,*,present=True,card=True,acp=True):
    j=Journal(tmp_path)
    atomic_json(j.md/'runtime.json',{'pid':1,'session':'synthetic'})
    atomic_json(j.md/'shop-health.json',{'checked_at':100,'status':'healthy','url':'https://own.example'})
    own={'name':'seochanit','alive':True,'card':{'url':'https://own.example'},'owner':{'login':'seochanit'}}
    def get(url,**kwargs):
        assert kwargs['timeout']==6 and kwargs['trust_env'] is False
        if url.endswith('/api/shops'):
            body={'shops':[{'name':'seochanit','url':'https://own.example' if present else 'https://other.example',
                             'owner':{'login':'seochanit'}}]}
        elif url.endswith('acp.json'):body={'protocol':'acp' if acp else 'wrong'}
        else:body={'name':'Seochanit Shop' if card else 'wrong'}
        return httpx.Response(200,json=body)
    monkeypatch.setattr('r3.transport_health.httpx.get',get)
    return j,own


def test_stale_heartbeat_can_recover_without_rotating_a_live_tunnel(tmp_path,monkeypatch):
    j,own=fixture(tmp_path,monkeypatch)
    result=refresh(j,own,1000)
    assert result['checked_at']==1000 and result['primary_heartbeat_at']==100
    assert result['status']=='healthy' and result['url']=='https://own.example'
    assert j.events('shop_health_independent_probe')[0]['healthy']


def test_wrong_registered_url_cannot_clear_the_transport_brake(tmp_path,monkeypatch):
    j,own=fixture(tmp_path,monkeypatch,present=False)
    assert refresh(j,own,1000) is None
    assert read_json(j.md/'shop-health.json')['checked_at']==100


def test_card_alone_is_not_evidence_of_a_working_checkout(tmp_path,monkeypatch):
    j,own=fixture(tmp_path,monkeypatch,acp=False)
    assert refresh(j,own,1000) is None


def test_runtime_change_during_probe_cannot_overwrite_new_health(tmp_path,monkeypatch):
    j,own=fixture(tmp_path,monkeypatch)
    original=j.snapshot;calls=[]
    def snapshot(name,default=None):
        if name=='runtime.json':
            calls.append(name)
            return {'pid':len(calls)}
        return original(name,default)
    monkeypatch.setattr(j,'snapshot',snapshot)
    assert refresh(j,own,1000) is None
