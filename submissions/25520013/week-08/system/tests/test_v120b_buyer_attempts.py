"""v1.20b: buyer attempt cap 3 -> 6 with a time guard. Attempts 1-3 and DEADLINE_S unchanged; attempts 4-6 only
within extra_attempt_window_s of the hook start and before expires - expiry_guard_s. No network: haggle is faked."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'hooks')):
    if p not in sys.path:
        sys.path.insert(0, p)
import policy as P  # noqa: E402
import market_buyer as MB  # noqa: E402

T0 = 1793756000.0


def test_policy_values_v120b():
    b = P.load()['buyer']
    assert (b['max_shops'], b['extra_attempt_window_s'], b['expiry_guard_s']) == (6, 60, 60)
    assert MB.DEADLINE_S == 200 and MB.BASE_SHOPS == 3


@pytest.mark.parametrize('n', [0, 1, 2])
def test_first_three_attempts_ignore_the_guard(n):
    pol = P.load()
    order = {'expires': T0 + 10}
    assert MB.attempt_allowed(n, 150, T0 + 150, order, pol) == (True, None)


def test_fourth_attempt_allowed_inside_window():
    assert MB.attempt_allowed(3, 30, T0 + 30, {'expires': T0 + 600}, P.load()) == (True, None)


def test_fourth_attempt_stopped_by_elapsed_guard():
    assert MB.attempt_allowed(3, 60, T0 + 60, {'expires': T0 + 600}, P.load()) == (False, 'guard_elapsed')


def test_fourth_attempt_stopped_by_expiry_guard():
    assert MB.attempt_allowed(3, 10, T0 + 10, {'expires': T0 + 69}, P.load()) == (False, 'guard_expiry')


def test_no_expires_field_uses_elapsed_only():
    assert MB.attempt_allowed(4, 10, T0 + 10, {}, P.load()) == (True, None)


def test_cap_six():
    pol = P.load()
    assert MB.attempt_allowed(5, 1, T0 + 1, {}, pol) == (True, None)
    assert MB.attempt_allowed(6, 1, T0 + 1, {}, pol) == (False, 'max_shops')


def test_deadline_still_first():
    assert MB.attempt_allowed(1, 201, T0 + 201, {}, P.load()) == (False, 'deadline')


def test_max_shops_3_is_v119_behaviour():
    pol = P.load()
    pol['buyer']['max_shops'] = 3
    for n in range(0, 5):
        for el in (0, 59, 61, 199, 201):
            ok, _ = MB.attempt_allowed(n, el, T0 + el, {'expires': T0 + 30}, pol)
            assert ok == (n < 3 and el <= 200)


def test_bad_max_shops_falls_back_to_three():
    pol = P.load()
    pol['buyer']['max_shops'] = 'six'
    assert MB.attempt_allowed(3, 1, T0 + 1, {}, pol) == (False, 'max_shops')


def test_attempt_deadline():
    pol = P.load()
    assert MB.attempt_deadline(2, T0, {'expires': T0 + 100}, pol) == T0 + 200
    assert MB.attempt_deadline(3, T0, {'expires': T0 + 100}, pol) == T0 + 40
    assert MB.attempt_deadline(3, T0, {}, pol) == T0 + 200


@pytest.mark.parametrize('k,v', [('max_shops', 0), ('max_shops', 9), ('max_shops', 6.0), ('max_shops', True),
                                 ('extra_attempt_window_s', -1), ('extra_attempt_window_s', 201),
                                 ('expiry_guard_s', 601), ('expiry_guard_s', 'x')])
def test_validate_rejects(k, v):
    pol = P.load()
    pol['buyer'][k] = v
    with pytest.raises(P.PolicyError):
        P.validate(pol)


class Clock:
    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t


def _run_main(monkeypatch, tmp_path, n_cands, step_s, expires, max_shops=None, deal_at=None):
    clock = Clock(T0)
    calls, rows, outs = [], [], []
    pol = P.load()
    if max_shops is not None:
        pol['buyer']['max_shops'] = max_shops
    order = {'order': 'o-test', 'budget': 100000, 'category': 'c', 'kind': 'goods', 'expires': expires}
    cands = [{'shop': {'url': 'http://s%d' % i, 'name': 's%d' % i}, 'sku': 'G%d' % i, 'name': 'n', 'score': 1.0,
              'list_price': 1000} for i in range(n_cands)]

    def fake_haggle(c, o, p, deadline, accept=None):
        calls.append((c['shop']['url'], deadline))
        clock.t += step_s
        if deal_at is not None and len(calls) == deal_at:
            return {'price': 900, 'item': c['sku']}, {'rounds': 1, 'state': 'TASK_STATE_COMPLETED'}
        return None, {'rounds': 1, 'state': 'TASK_STATE_REJECTED'}

    class Rec:
        def __init__(self, *a, **k):
            pass

        def write(self, decision, seq, **k):
            rows.append(k)

    class Rep:
        def __init__(self, *a, **k):
            pass

        def update(self):
            pass

        def table(self, prior):
            return {}

    monkeypatch.setattr(MB, 'time', clock)
    monkeypatch.setattr(MB, 'haggle', fake_haggle)
    monkeypatch.setattr(MB, 'Recorder', Rec)
    monkeypatch.setattr(MB, 'Reputation', Rep)
    monkeypatch.setattr(MB, 'ingest', lambda md, collect=None: (0, {}, 0.0))
    monkeypatch.setattr(MB, 'read_json', lambda path, d=None: order if str(path).endswith('order.json') else d)
    monkeypatch.setattr(MB.OW, 'resolve', lambda pol: {'names': set(), 'urls': set(), 'pubkeys': {},
                                                       'source': 'test', 'error': None})
    monkeypatch.setattr(MB, 'volume_level', lambda side, pol: (0.0, 0))
    monkeypatch.setattr(MB, 'catch_up_stats', lambda catalog: (None, {}, None))
    monkeypatch.setattr(MB.P, 'load', lambda *a, **k: dict(pol, _hash='test'))
    monkeypatch.setattr(MB.P, 'choose_offers', lambda *a, **k: cands)
    monkeypatch.setattr(MB.P, 'accept_policy', lambda *a, **k: {})
    monkeypatch.setattr(MB, 'hook_event', lambda *a, **k: 1)
    monkeypatch.setattr(MB, 'emit', lambda out: outs.append(out))
    monkeypatch.setattr(sys, 'argv', ['market_buyer.py', '--shops', str(tmp_path / 'shops.json'),
                                      '--order', str(tmp_path / 'order.json')])
    MB.main()
    return calls, rows[-1]['outcome'], outs[-1]


def test_main_tries_six_shops_when_fast(monkeypatch, tmp_path):
    calls, out, _ = _run_main(monkeypatch, tmp_path, 8, 5, T0 + 600)
    assert len(calls) == 6 and out['tried'] == 6 and out['stop'] == 'max_shops' and out['guard_hit'] is False


def test_main_v119_three_when_max_shops_3(monkeypatch, tmp_path):
    calls, out, _ = _run_main(monkeypatch, tmp_path, 8, 5, T0 + 600, max_shops=3)
    assert len(calls) == 3 and out['stop'] == 'max_shops'


def test_main_elapsed_guard(monkeypatch, tmp_path):
    calls, out, _ = _run_main(monkeypatch, tmp_path, 8, 25, T0 + 600)
    # 3 attempts take 75 s >= 60 s window: no 4th attempt
    assert len(calls) == 3 and out['stop'] == 'guard_elapsed' and out['guard_hit'] is True


def test_main_expiry_guard(monkeypatch, tmp_path):
    calls, out, _ = _run_main(monkeypatch, tmp_path, 8, 5, T0 + 80)
    # after 3 attempts now = T0+15 < 80-60=20 -> 4th allowed (deadline T0+20); after it now = T0+20 -> stop
    assert len(calls) == 4 and calls[3][1] == T0 + 20 and out['stop'] == 'guard_expiry'


def test_main_first_three_deadlines_unchanged(monkeypatch, tmp_path):
    calls, _, _ = _run_main(monkeypatch, tmp_path, 8, 5, T0 + 600)
    assert [d for _, d in calls[:3]] == [T0 + 200] * 3 and calls[3][1] == T0 + 200


def test_main_stops_at_deal(monkeypatch, tmp_path):
    calls, out, o = _run_main(monkeypatch, tmp_path, 8, 5, T0 + 600, deal_at=5)
    assert len(calls) == 5 and out['deal'] is True and o['receipt']['price'] == 900 and out['stop'] is None


def test_main_fewer_candidates_than_cap(monkeypatch, tmp_path):
    calls, out, _ = _run_main(monkeypatch, tmp_path, 2, 5, T0 + 600)
    assert len(calls) == 2 and out['stop'] is None and out['guard_hit'] is False
