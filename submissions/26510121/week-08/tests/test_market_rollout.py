import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import deploy_shop_when_ready as rollout


def test_no_rollout_when_market_discovery_is_empty_or_our_url_differs(monkeypatch):
    monkeypatch.setattr(rollout,'read_json',lambda *a:{'url':'https://ours','status':'healthy','checked_at':100})
    for rows in ([],[{'name':'seochanit','url':'https://old'}, {'name':'a'},{'name':'b'}]):
        def get(url,**kwargs):
            assert url.endswith('/api/shops')  # No outside card requests or restarts.
            return SimpleNamespace(status_code=200,json=lambda:{'shops':rows})
        assert rollout.probe(SimpleNamespace(get=get),100)['ready'] is False


def test_rollout_requires_live_own_card_and_fresh_local_health(monkeypatch):
    rows=[{'name':'seochanit','url':'https://ours'},{'name':'a'},{'name':'b'}]
    def get(url,**kwargs):
        return SimpleNamespace(status_code=200,json=lambda:{'shops':rows})
    client=SimpleNamespace(get=get)
    for checked,ready in [(100,True),(-100,False)]:
        monkeypatch.setattr(rollout,'read_json',lambda *a:{'url':'https://ours','status':'healthy','checked_at':checked})
        assert rollout.probe(client,100)['ready'] is ready


def test_healthy_local_shop_can_recover_after_peers_return_without_deadlocking(monkeypatch):
    monkeypatch.setattr(rollout,'read_json',lambda *a:{'url':'https://ours','status':'healthy','checked_at':100})
    def get(url,**kw):
        return SimpleNamespace(status_code=200,json=lambda:{'shops':[{'name':str(n)} for n in range(3)]})
    state=rollout.probe(SimpleNamespace(get=get),100)
    assert state['ready'] and state['requires_reregistration']
