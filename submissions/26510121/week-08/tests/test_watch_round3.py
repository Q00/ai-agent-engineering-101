"""Exercise the round boundary and actual OS lease without live market actions."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from r3.lifecycle import lease, market_active, request_stop
from r3.state import atomic_json
from watch_round3 import Watcher, INTERVAL


def root(tmp_path):
    atomic_json(tmp_path / 'policy.json', {'end_at': '2099-11-17T23:59:00+09:00'})
    return tmp_path


def test_waits_ten_minutes_then_launches_both_roles_once(tmp_path):
    folder = root(tmp_path)
    rounds = iter([2, 3])
    launched = []
    child = SimpleNamespace(pid=123, returncode=None, poll=lambda: None)

    def spawn(log):
        launched.append(log)
        log.write_text('[market] shop registered as student, round 3\n'
                       '[market] buyer registered as student, round 3\n', encoding='utf-8')
        return child

    w = Watcher(folder, fetch=lambda: next(rounds), spawn=spawn, active=lambda r: False)
    assert w.tick(1000) and not launched and w.next_check == 1000 + INTERVAL
    assert w.tick(1599) and not launched
    assert w.tick(1600) and len(launched) == 1
    assert not w.state['both_roles_registered']  # Launch alone is not success.
    assert w.tick(1601) and w.state['both_roles_registered']
    assert w.tick(2200) and len(launched) == 1


def test_round_four_never_launches(tmp_path):
    w = Watcher(root(tmp_path), fetch=lambda: 4, spawn=lambda p: pytest.fail('must not start'))
    assert not w.tick(1000)
    assert not w.state['market_join_attempted']


def test_existing_manual_runtime_prevents_duplicate_join(tmp_path):
    folder = root(tmp_path)
    with lease(folder / '.market/runtime.lock'):
        assert market_active(folder)
        w = Watcher(folder, fetch=lambda: 3, spawn=lambda p: pytest.fail('duplicate'))
        assert w.tick(1000) and w.state['duplicate_launch_skipped']
    assert not market_active(folder)


def test_network_failure_waits_before_retry_without_assuming_open(tmp_path):
    calls = []

    def fetch():
        calls.append(True)
        raise OSError('synthetic network failure')

    w = Watcher(root(tmp_path), fetch=fetch, spawn=lambda p: pytest.fail('no verified round'))
    assert w.tick(1000) and w.state['status'] == 'check_or_start_failed'
    assert w.tick(1599) and len(calls) == 1
    assert w.tick(1600) and len(calls) == 2


def test_failed_join_retries_at_next_interval(tmp_path):
    folder = root(tmp_path)
    launches = []
    child = SimpleNamespace(pid=123, returncode=1, poll=lambda: 1)

    def spawn(log):
        launches.append(log)
        log.touch()
        return child

    w = Watcher(folder, fetch=lambda: 3, spawn=spawn, active=lambda r: False)
    assert w.tick(1000) and len(launches) == 1
    assert w.tick(1001) and not w.state['both_roles_registered']
    assert w.tick(1600) and len(launches) == 2


def test_deadline_and_stop_do_not_launch_and_stop_is_pid_scoped(tmp_path):
    folder = root(tmp_path)
    w = Watcher(folder, fetch=lambda: pytest.fail('deadline'), spawn=lambda p: pytest.fail('deadline'))
    assert not w.tick(w.end)
    atomic_json(folder / '.market/runtime.json', {'pid': 123, 'session': 'synthetic-session'})
    request_stop(999, folder)
    assert not (folder / '.market/runtime-stop.json').exists()
    request_stop(123, folder)
    assert json.loads((folder / '.market/runtime-stop.json').read_text())['session'] == 'synthetic-session'
