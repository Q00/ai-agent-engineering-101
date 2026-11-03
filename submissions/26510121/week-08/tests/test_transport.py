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
])
def test_launch_preserves_route_and_strips_credentials(tmp_path, monkeypatch, provider, url_line):
    monkeypatch.setattr(transport.httpx, 'get', lambda *a, **kw: httpx.Response(
        200, json={'name': 'Seochanit Shop'}, request=httpx.Request('GET', a[0])))
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-only')
    monkeypatch.setenv('MARKET_OWNER', 'synthetic-owner')
    calls = []
    proc = SimpleNamespace(stdout=io.StringIO(url_line + '\n'), poll=lambda: None)

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
    else:
        assert command[command.index('--protocol') + 1] == 'http2'
        assert command[command.index('--edge-ip-version') + 1] == '4'


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
