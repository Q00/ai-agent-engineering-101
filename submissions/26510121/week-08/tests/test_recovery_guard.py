"""Regression for a real double health-write failure and bounded fallback repair."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3 import state as storage
from r3.transport import supervision_step
import shop_recovery_guard as guard
from shop_recovery_guard import decide


def windows_denial():
    error = PermissionError('synthetic sharing/access denial')
    error.winerror = 5
    return error


def test_transient_replace_keeps_complete_json_and_bounds_delay(tmp_path, monkeypatch):
    path = tmp_path / 'health.json'
    storage.atomic_json(path, {'old': True})
    original = storage.os.replace
    attempts, sleeps = [], []
    def replace(source, target):
        attempts.append(1)
        if len(attempts) < 3:
            assert json.loads(path.read_text()) == {'old': True}
            raise windows_denial()
        original(source, target)
    monkeypatch.setattr(storage.os, 'replace', replace)
    monkeypatch.setattr(storage.time, 'sleep', sleeps.append)
    storage.atomic_json(path, {'new': True})
    assert json.loads(path.read_text()) == {'new': True}
    assert len(attempts) == 3 and sum(sleeps) == pytest.approx(.03)


def test_persistent_denial_preserves_old_state_and_stops_retrying(tmp_path, monkeypatch):
    path = tmp_path / 'health.json'
    storage.atomic_json(path, {'old': True})
    sleeps = []
    monkeypatch.setattr(storage.os, 'replace', lambda *a: (_ for _ in ()).throw(windows_denial()))
    monkeypatch.setattr(storage.time, 'sleep', sleeps.append)
    with pytest.raises(PermissionError):
        storage.atomic_json(path, {'new': True})
    assert len(sleeps) == 3 and sum(sleeps) == pytest.approx(.07)
    assert json.loads(path.read_text()) == {'old': True}


def test_non_windows_permission_failure_is_not_silently_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.os, 'replace', lambda *a: (_ for _ in ()).throw(PermissionError('permanent ACL')))
    monkeypatch.setattr(storage.time, 'sleep', lambda *a: pytest.fail('unexpected retry'))
    with pytest.raises(PermissionError):
        storage.atomic_json(tmp_path / 'x.json', {})


def test_supervisor_survives_failure_inside_its_error_report():
    calls = []
    def tick():
        calls.append('tick')
        if len(calls) == 1:
            raise windows_denial()
    monitor = SimpleNamespace(tick=tick, snapshot=lambda *a: (_ for _ in ()).throw(windows_denial()))
    kit = SimpleNamespace(log=lambda *a: None)
    assert supervision_step(monitor, kit) is False
    assert supervision_step(monitor, kit) is True
    assert calls == ['tick', 'tick']


def ready(**patch):
    return dict(runtime_matches=True, primary_age=1000, verified=True, identity_matches=True,
                public_ready=True, registry_count=10, listed=True, shop_alive=False, active_peers=9, **patch)


def test_two_confirmations_and_cooldown_are_required():
    action, state, _ = decide(ready(), {}, 1000)
    assert action == 'wait'
    action, state, _ = decide(ready(), state, 1030)
    assert action == 'repair' and state['last_attempt'] == 1030
    _, state, _ = decide(ready(), state, 1060)
    action, state, reason = decide(ready(), state, 1090)
    assert action == 'wait' and reason == 'repair_cooldown'
    assert decide(ready(), state, 1150)[0] == 'repair'


@pytest.mark.parametrize('patch', [
    {'primary_age': 30}, {'verified': False}, {'identity_matches': False},
    {'public_ready': False}, {'registry_count': 0}, {'active_peers': 0},
    {'shop_alive': None}, {'shop_alive': True},
])
def test_unknown_outage_healthy_or_primary_owned_state_never_repairs(patch):
    probe = ready(); probe.update(patch)
    assert decide(probe, {'misses': 10}, 1000)[0] != 'repair'


def test_runtime_change_stops_guard():
    probe = ready(); probe['runtime_matches'] = False
    assert decide(probe, {'misses': 10}, 1000)[0] == 'stop'


def test_repair_rechecks_runtime_before_spawning_helper(monkeypatch):
    monkeypatch.setattr(guard, 'read_json', lambda *a: {'session': 'new'})
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: pytest.fail('must not register'))
    assert guard.repair({'session': 'old'}, 'https://own.example')['attempted'] is False


def test_helper_receives_no_model_key_and_has_a_wall_timeout(monkeypatch):
    pinned = {'session': 'old'}
    monkeypatch.setattr(guard, 'read_json', lambda path, default: pinned if path.name == 'runtime.json' else {'url': 'https://own.example'})
    monkeypatch.setattr(guard, 'market_active', lambda: True)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-only')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'synthetic-only')
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'synthetic-secret-must-not-be-logged')
    monkeypatch.setattr(guard.subprocess, 'run', run)
    result = guard.repair(pinned, 'https://own.example')
    assert calls[0][0][1].endswith('restore_shop_registration.py')
    assert calls[0][1]['timeout'] == 55
    assert 'OPENAI_API_KEY' not in calls[0][1]['env'] and 'ANTHROPIC_API_KEY' not in calls[0][1]['env']
    assert 'stdout' not in result and result['registration_acceptance_is_not_recovery_proof']


def test_probe_network_error_never_looks_verified(monkeypatch):
    monkeypatch.setattr(guard.httpx.Client, 'get', lambda *a, **k: (_ for _ in ()).throw(guard.httpx.ReadTimeout('synthetic')))
    monkeypatch.setattr(guard, 'read_json', lambda *a: {'session': 'pinned'})
    monkeypatch.setattr(guard, 'market_active', lambda: True)
    result = guard.probe('https://own.example', {'session': 'pinned'})
    assert result['verified'] is False
    assert decide(result, {'misses': 10}, 1000)[0] != 'repair'
