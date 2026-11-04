"""Regressions for ALL selection, negotiation concessions and capital rotation."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3.state import Journal, atomic_json, policy
from r3.meta import rank_gain, rank_progress, rank_targets
from r3.model import Chooser
from r3.market_memory import market_book, buyer_price_actions, buyer_terms
from r3.economy import restock_candidates, cash_capacity, buyer_candidates
from r3.adaptive import activate_revision, evidence, flow, KEY, MATURITY


def score_board():
    return {'round': 3, 'buyers': [
        {'name': 'seochanit', 'surplus': 1000, 'deals': 1, 'forum': .6, 'posts': 1},
        {'name': 'peer', 'surplus': 1000000, 'deals': 2, 'forum': .7, 'posts': 3}]}


def test_all_fallback_prefers_rank_gain_over_larger_raw_surplus(tmp_path, monkeypatch):
    monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    j = Journal(tmp_path)
    atomic_json(j.md / 'board.json', score_board())
    candidates = [dict(id='raw', score=10000, expected_delta={'surplus': 10000}),
                  dict(id='all', score=100, expected_delta={'surplus': 100, 'deals': 2})]
    assert Chooser(j).choose('buyer', candidates, {})['id'] == 'all'
    assert j.events('prediction')[0]['context']['next_rank_thresholds']['deals']['gap'] == 1


def test_inactive_buyers_do_not_dilute_threshold_gain_and_small_gains_have_progress():
    board = score_board()
    base = rank_gain(board, 'buyer', 'seochanit', {'deals': 2})
    board['buyers'] += [dict(name='inactive', surplus=0, deals=0, posts=0, forum=0)] * 100
    assert rank_gain(board, 'buyer', 'seochanit', {'deals': 2}) == base
    assert rank_gain(board, 'buyer', 'seochanit', {'surplus': 100}) == 0
    assert rank_progress(board, 'buyer', 'seochanit', {'surplus': 100}) > 0
    assert rank_targets(board, 'buyer')['surplus']['gap'] == 999000


def test_actor_cannot_discard_numerical_all_for_unjustified_raw_money(tmp_path, monkeypatch):
    monkeypatch.delenv('R3_MODEL_MODE', raising=False)
    j = Journal(tmp_path)
    atomic_json(j.md / 'board.json', score_board())
    candidates = [dict(id='raw', score=10000, expected_delta={'surplus': 10000}),
                  dict(id='all', score=100, expected_delta={'surplus': 100, 'deals': 2})]
    chooser = Chooser(j)
    monkeypatch.setattr(chooser, '_call', lambda *a, **k: candidates[0])
    assert chooser.choose('buyer', candidates, {})['id'] == 'all'
    assert j.events('critic_override')


def test_concessions_are_monotone_and_last_turn_accepts_feasible_price():
    product = {'list_price': 400000}
    previous = None
    bids = []
    for turn, ask in enumerate([387265, 355555, 334944]):
        moves = buyer_price_actions(ask, 350000, product, 296667, .92, turn, 4, previous)
        move = next(m for m in moves if m['id'] == 'counter')
        bids.append(move['price']); previous = move['price']
    assert bids[0] < bids[1] < bids[2] <= 350000
    last = buyer_price_actions(325000, 350000, product, 296667, .92, 3, 4, previous)
    assert [m['id'] for m in last] == ['accept']
    assert not buyer_price_actions(360000, 350000, product, 296667, .92, 3, 4, previous)


def test_uninsured_cap_and_risk_adjusted_surplus_stay_enforced(tmp_path):
    j = Journal(tmp_path)
    product = {'sku': 'G1', 'kind': 'goods', 'list_price': 10000}
    cap, required, stress = buyer_terms(product, {'budget': 10000}, {}, j, policy())
    assert cap == 7000 and required == 0 and stress == .3
    actions = buyer_price_actions(7000, cap, product, 8000, .92, 3, 4,
                                  failure_stress=stress, covered=False)
    assert actions[0]['expected_delta']['surplus'] == 0


def stocked_catalog(j, now):
    catalog = {'G1': {'category': 'furniture', 'list_price': 300000, 'kind': 'goods'},
               'G2': {'category': 'food', 'list_price': 10000, 'kind': 'food', 'shelf_hours': 24},
               'G3': {'category': 'furniture', 'list_price': 250000, 'kind': 'goods'}}
    for sku, price in [('G1', 240000), ('G2', 8000), ('G3', 205000)]:
        for n in range(40):
            j.add('public_settle', {'item': sku, 'amount': price, 'shop': 'peer' + str(n % 2), 'grade': 'A'},
                  eid=f'public:{sku[1:]}{n:03}', ts=now - 100 - n)
    atomic_json(j.md / 'catalog.json', catalog)
    return catalog


def test_observed_high_value_goods_compete_with_food_without_relaxing_reserves(tmp_path):
    now = time.time()
    j = Journal(tmp_path)
    cat = stocked_catalog(j, now)
    inv = {'balance': 5000000, 'units': []}
    candidates = restock_candidates(inv, cat, [], j, policy(), now)
    assert candidates[0]['sku'] in {'G1', 'G3'}
    assert {'G1', 'G3'} <= {c['sku'] for c in candidates}  # Same category need not exclude another SKU.
    capacity = cash_capacity(inv, j, policy(), now)
    assert sum(c['capital_commitment'] for c in candidates) <= capacity['spendable']
    assert all(c['capital_commitment'] <= .15 * inv['balance'] for c in candidates)
    assert all(c['evidence']['expected_holding_hours'] < capacity['remaining_days'] * 24 for c in candidates)


def test_wrong_sku_failures_do_not_poison_new_sku_and_refs_are_real(tmp_path):
    now = time.time()
    j = Journal(tmp_path)
    cat = stocked_catalog(j, now)
    for n in range(20):
        j.add('attempt', {'shop': 'url', 'sku': 'G999', 'order': str(n), 'success': False, 'kind': 'no_agreement'})
    candidates = buyer_candidates({'acceptable': ['G1'], 'budget': 300000},
        [{'name': 'peer', 'url': 'url', 'offers': [{'sku': 'G1', 'qty': 1}]}], cat, j)
    assert candidates[0]['reference_price'] == 240000
    assert candidates[0]['evidence']['peer_failures'] == 0
    book = market_book(j, cat, now)
    assert book['G1']['samples'] == 40 and all(type(s) is int for s in book['G1']['refs'])


def test_revision_excludes_old_outage_and_unexecuted_new_orders(tmp_path):
    j = Journal(tmp_path)
    now = time.time()
    activate_revision(j, 'test', now - MATURITY - 1000)
    version = j.get(KEY + 'buyer')['version']
    ts = now - MATURITY - 100
    for oid in ['outage', 'executed']:
        j.add('buyer_order', {'order': oid, 'strategy_version': version}, ts=ts)
        j.add('market_result', {'order': oid, 'verified': False}, ts=ts + 1)
    j.add('buyer_execution', {'order': 'executed'}, ts=ts)
    assert evidence(j, 'buyer', version, now)['samples'] == 1
    activate_revision(j, 'test', now)
    assert j.get(KEY + 'buyer')['version'] == version


def test_adaptive_flow_observes_all_even_when_money_moves_opposite():
    start = {'at': 0, 'buyer': {'own': 100, 'peers': {}, 'own_all': .8, 'peer_all': {}}}
    end = {'at': 3600, 'buyer': {'own': 200, 'peers': {}, 'own_all': .7, 'peer_all': {}}}
    observed = flow(start, end, 'buyer')
    assert observed['own_per_hour'] > 0 and observed['own_all_per_hour'] < 0


def test_forum_comparison_is_sourced_and_caveated(tmp_path):
    from r3.forum import schedule
    j = Journal(tmp_path)
    now = time.time()
    stocked_catalog(j, now)
    j.put('board_round', 3)
    j.add('purchase', {'sku': 'G1', 'price': 230000, 'ledger': 999, 'paid_at': now,
                       'grade': 'A', 'warranty_hours': 6})
    schedule(j, policy(), now)
    comparison = next(p for _, p in j.pending_posts() if p['priority'] == 4)
    assert comparison['refs'][0] == 999 and len(comparison['refs']) > 1
    assert 'median=240000' in comparison['post']
    assert 'not live quotes' in comparison['post'] and 'warranty differ' in comparison['post']


def test_signed_immediate_offer_is_usable_but_above_risk_cap_is_rejected(tmp_path, monkeypatch):
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from r3.protocol import canon, signed_body
    from test_market_contract import pub
    from market_buyer import buy
    monkeypatch.setenv('R3_MODEL_MODE', 'offline')
    key = Ed25519PrivateKey.generate()
    class Peer:
        def discover(self, url): pass
        def send(self, text, task=None):
            rec = {'shop': 'shop', 'item': 'G1', 'price': 8000, 'task': 't',
                   'cert': True, 'warranty_hours': self.warranty}
            rec['sig_seller'] = base64.b64encode(key.sign(canon(signed_body(rec)))).decode()
            return {'id': 't', 'status': {'state': 'TASK_STATE_COMPLETED'},
                    'artifacts': [{'parts': [{'data': rec}]}]}
        def close(self): pass
    for warranty, usable in [(6, True), (0, False)]:
        j = Journal(tmp_path / str(warranty))
        atomic_json(j.md / 'catalog.json', {'G1': {'kind': 'goods', 'list_price': 10000}})
        atomic_json(j.md / 'registry.json', {'url': {'pubkey': pub(key)}})
        Peer.warranty = warranty
        result = buy({'order': 'o', 'acceptable': ['G1'], 'kind': 'goods', 'budget': 10000,
                      'expires': time.time() + 300},
                     [{'name': 'peer', 'url': 'url', 'offers': [{'sku': 'G1', 'qty': 1}]}], j, peer_factory=Peer)
        assert bool(result['receipt']) is usable
        if usable:
            assert result['accept']['cert'] and result['accept']['warranty_hours'] == 6
