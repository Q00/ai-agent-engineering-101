"""Test process boundaries; public connectivity has a separate real probe."""
import io
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import pytest
from r3 import transport


@pytest.mark.parametrize('provider,url_line', [
    ('pinggy', 'TUNNEL_URL https://synthetic.free.pinggy.net'),
    ('cloudflare', 'https://synthetic.trycloudflare.com'),
    ('localhost', 'https://synthetic.lhr.life'),
])
def test_launch_preserves_route_and_strips_credentials(tmp_path, monkeypatch, provider, url_line):
    monkeypatch.setattr(transport.httpx, 'get', lambda *a, **kw: httpx.Response(
        200, json={'name': 'Seochanit Shop'}, request=httpx.Request('GET', a[0])))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-only')
    monkeypatch.setenv('MARKET_OWNER', 'synthetic-owner')
    calls = []
    banner = 'Manage tunnels at https://admin.localhost.run\n' if provider == 'localhost' else ''
    proc = SimpleNamespace(stdout=io.StringIO(banner + url_line + '\n'), poll=lambda: None)

    def launch(command, **kwargs):
        calls.append((command, kwargs))
        return proc

    monkeypatch.setattr(transport.subprocess, 'Popen', launch)
    out, url = transport.open_tunnel(SimpleNamespace(stop=lambda p: None), 9999, provider,
                                     tmp_path / 'tunnel.txt')
    command, kwargs = calls[0]
    assert out is proc and url == url_line.split()[-1]
    assert 'OPENAI_API_KEY' not in kwargs['env'] and 'MARKET_OWNER' not in kwargs['env']
    assert kwargs['stdin'] == transport.subprocess.DEVNULL
    if provider == 'pinggy':
        assert command[0] == sys.executable and command[-2:] == ['--port', '9999']
    elif provider=='cloudflare':
        assert command[command.index('--protocol') + 1] == 'http2'
        assert command[command.index('--edge-ip-version') + 1] == '4'
    else:
        assert 'nokey@localhost.run' in command and 'IdentityFile=none' in command
        assert 'StrictHostKeyChecking=accept-new' in command
        assert '80:127.0.0.1:9999' in command


def test_wrong_local_service_is_never_exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(transport.httpx, 'get', lambda *a, **kw: httpx.Response(
        200, json={'name': 'unrelated service'}, request=httpx.Request('GET', a[0])))
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises(RuntimeError, match='unexpected local service'):
        transport.open_tunnel(None, 9999, 'pinggy', tmp_path / 'tunnel.txt')


def test_failed_tunnel_is_stopped_and_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(transport.httpx, 'get', lambda *a, **kw: httpx.Response(
        200, json={'name': 'Seochanit Shop'}, request=httpx.Request('GET', a[0])))
    proc = SimpleNamespace(stdout=io.StringIO('synthetic provisioning failure\n'), poll=lambda: 1)
    monkeypatch.setattr(transport.subprocess, 'Popen', lambda *a, **kw: proc)
    stopped = []
    log = tmp_path / 'tunnel.txt'
    with pytest.raises(RuntimeError, match='did not provision'):
        transport.open_tunnel(SimpleNamespace(stop=stopped.append), 9999, 'pinggy', log)
    assert stopped == [proc]
    assert 'synthetic provisioning failure' in log.read_text(encoding='utf-8')


def supervisor_fixture(tmp_path, join, *, wait_card=lambda *a: True, open_tunnel=None):
    old = SimpleNamespace(poll=lambda: None)
    new = SimpleNamespace(poll=lambda: None)
    seller = SimpleNamespace(poll=lambda: None)
    stopped, logs = [], []
    kit = SimpleNamespace(card_ok=lambda url: True, wait_card=wait_card, stop=stopped.append,
                          log=logs.append, open_tunnel=open_tunnel or (lambda port: (new, 'https://new.example')))
    proxy = SimpleNamespace(public='https://old.example')
    procs = [seller, old]
    monitor = transport.ShopSupervisor(kit, SimpleNamespace(tunnel=True, name='seochanit'), {}, tmp_path,
        SimpleNamespace(join=join), {'shop': 'synthetic'}, procs, proxy, 8000, 8001, {}, now=0)
    return monitor, old, new, stopped, logs


def test_healthy_new_tunnel_still_retries_failed_market_registration(tmp_path):
    attempts = []
    def join(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise SystemExit('synthetic transient agent card timeout')
        return {'name': 'seochanit'}
    monitor, old, new, stopped, logs = supervisor_fixture(tmp_path, join)
    monitor.tick(3301)
    assert monitor.pending_registration and monitor.procs[1] is new
    assert old not in stopped and old in monitor.procs
    monitor.tick(3316)
    assert len(attempts) == 2 and all(kwargs['tries'] == 1 for _, kwargs in attempts)
    assert not monitor.pending_registration and old in stopped
    assert len(monitor.procs) == 2 and monitor.procs[1] is new


def test_renewal_keeps_old_tunnel_alive_until_new_registration(tmp_path):
    events = []
    monitor, old, new, stopped, logs = supervisor_fixture(tmp_path,
        lambda *a, **kw: events.append(('join', old in stopped)) or {'name': 'seochanit'})
    monitor.tick(3299)
    assert not events and monitor.procs[1] is old
    monitor.tick(3300)
    assert events == [('join', False)] and old in stopped


def test_failed_replacement_discovery_preserves_existing_route(tmp_path):
    monitor, old, new, stopped, logs = supervisor_fixture(tmp_path,
        lambda *a, **kw: pytest.fail('must not register an unreachable replacement'), wait_card=lambda *a: False)
    with pytest.raises(RuntimeError):
        monitor.tick(3301)
    assert stopped == [new] and monitor.procs[1] is old and monitor.proxy.public == 'https://old.example'


def test_cloudflare_does_not_inherit_pinggy_renewal_deadline(tmp_path):
    monitor, old, new, stopped, logs = supervisor_fixture(tmp_path,
        lambda *a, **kw: pytest.fail('must not rotate a healthy Cloudflare tunnel'))
    monitor.provider = 'cloudflare'
    monitor.tick(7200)
    assert monitor.procs[1] is old and not stopped


def test_unavailable_preferred_transport_falls_back_and_tracks_provider(monkeypatch,tmp_path):
    calls=[];process=SimpleNamespace()
    monkeypatch.setenv('MARKET_TUNNEL_PROVIDER','pinggy')
    monkeypatch.setattr(transport,'ROOT',tmp_path)
    def launch(kit,port,provider):
        calls.append(provider)
        if provider=='pinggy':raise RuntimeError('native authentication timeout')
        return process,'https://working.trycloudflare.com'
    monkeypatch.setattr(transport,'open_tunnel',launch)
    actual,url=transport.open_available_tunnel(SimpleNamespace(log=lambda x:None),9999)
    assert calls==['pinggy','cloudflare'] and actual is process and process.r3_provider=='cloudflare'


def test_next_restart_uses_recently_verified_transport(monkeypatch,tmp_path):
    from r3.state import atomic_json
    import time
    monkeypatch.setattr(transport,'ROOT',tmp_path)
    monkeypatch.setenv('MARKET_TUNNEL_PROVIDER','pinggy')
    atomic_json(tmp_path/'.market/transport-provider.json',{'provider':'cloudflare','at':time.time()})
    calls=[]
    def launch(kit,port,provider):calls.append(provider);return SimpleNamespace(),'https://working.trycloudflare.com'
    monkeypatch.setattr(transport,'open_tunnel',launch)
    transport.open_available_tunnel(SimpleNamespace(log=lambda x:None),9999)
    assert calls==['cloudflare']


def test_global_empty_market_does_not_trigger_rejoin_or_rotate(tmp_path):
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,
        lambda *a,**kw:pytest.fail('no registration storm during a global outage'))
    monitor.provider='cloudflare'
    monitor.market.base='https://market.example'
    monitor.market.c=SimpleNamespace(get=lambda *a,**k:httpx.Response(200,json={'shops':[]}))
    monitor.tick(1);monitor.tick(31);monitor.tick(61)
    assert not monitor.pending_registration and monitor.procs[1] is old and not stopped
    assert monitor.market_visibility=='market_empty'


def test_remote_missing_shop_rejoins_even_when_card_is_locally_healthy(tmp_path):
    attempts=[]
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,
        lambda *a,**kw:attempts.append(a) or {'name':'seochanit'})
    monitor.provider='cloudflare'
    monitor.market.base='https://market.example'
    monitor.market.c=SimpleNamespace(get=lambda *a,**k:httpx.Response(200,json={'shops':[{'name':'peer'}]}))
    monitor.tick(1);assert not attempts
    monitor.tick(31)
    assert len(attempts)==1 and monitor.procs[1] is old and not stopped
    assert not monitor.pending_registration


def test_pending_registration_waits_when_the_whole_registry_disappears(tmp_path):
    calls=[]
    def join(*a,**kw):calls.append(a);raise SystemExit('market cannot read card')
    monitor,old,new,stopped,logs=supervisor_fixture(tmp_path,join)
    monitor.provider='cloudflare';monitor.market.base='https://market.example'
    rows=[{'name':'peer'}]
    monitor.market.c=SimpleNamespace(get=lambda *a,**kw:httpx.Response(200,json={'shops':rows}))
    monitor.tick(1);monitor.tick(31)
    assert monitor.pending_registration and len(calls)==1
    rows.clear();monitor.tick(61)
    assert len(calls)==1 and monitor.procs[1] is old and not stopped
