import concurrent.futures
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from r3.state import Journal, atomic_json, policy, identity
from r3.adaptive import (KEY, MATURITY, begin_buyer, effective, validate, update,
                         evidence, public_context, board_snapshot)
from r3.economy import restock_candidates

NOW = 1793700000.0


def board(j, now, buyer=6000, shop=60000, peers=600):
    atomic_json(j.md / 'board.json', {'round': 3,
        'buyers': [{'name': 'seochanit', 'surplus': buyer}] +
                  [{'name': name, 'surplus': peers} for name in ['a', 'b', 'c']],
        'shops': [{'name': 'seochanit', 'profit': shop}] +
                 [{'name': name, 'profit': peers} for name in ['a', 'b', 'c']]})
    j.put('board_checked_at', now)
    j.put('board_round', 3)


def baseline(j):
    board(j, NOW - MATURITY, buyer=0, shop=0, peers=0)
    j.add('strategy_market', board_snapshot(j, NOW - MATURITY), ts=NOW - MATURITY)
    board(j, NOW)


def buyer_events(j, version=0, start=NOW - MATURITY - 100, paid=0):
    for n in range(20):
        oid = f'v{version}-o{n}'
        j.add('buyer_order', {'order': oid, 'budget': 1000, 'strategy_version': version}, ts=start)
        j.add('buyer_counter', {'order': oid, 'strategy_version': version}, ts=start)
        j.add('market_result', {'order': oid, 'verified': n < paid}, ts=start + 10)
        if n < paid:
            j.add('purchase', {'order': oid, 'value': 1000, 'price': 500}, ts=start + 10)
            j.add('outcome', {'order': oid, 'matured': True, 'broken': False}, ts=start + MATURITY)
        else:
            j.add('attempt', {'order': oid, 'kind': 'no_agreement'}, ts=start)


def test_only_bounded_strategy_fields_can_override_policy(tmp_path):
    j = Journal(tmp_path)
    for patch in [{'daily_api_budget_usd': 100}, {'buyer_counter_ratio': .5}, {'buyer_counter_ratio': float('nan')}]:
        with pytest.raises(ValueError):
            validate('buyer', patch)
    for patch in [{'inventory_target_units': 100}, {'category_weights': {'x': 10}}, {'actor_model': 'other'}]:
        with pytest.raises(ValueError):
            validate('shop', patch)
    j.put(KEY + 'buyer', {'version': 1, 'patch': {'buyer_counter_ratio': .94}})
    cfg, selected = effective(j, 'buyer')
    assert cfg['daily_api_budget_usd'] == policy()['daily_api_budget_usd']
    assert cfg['daily_api_budget_usd'] is None  # User requested observed spend without a cap.
    assert cfg['buyer_counter_ratio'] == .94 and selected['version'] == 1


def test_order_retry_keeps_original_strategy_version(tmp_path):
    j = Journal(tmp_path)
    order = {'order': 'one', 'budget': 1000}
    first, original = begin_buyer(j, order)
    j.put(KEY + 'buyer', {'version': original['version']+1, 'patch': {'buyer_counter_ratio': .96}})
    retry, selected = begin_buyer(j, order)
    new, _ = begin_buyer(j, {'order': 'two', 'budget': 1000})
    assert first['buyer_counter_ratio'] == retry['buyer_counter_ratio'] == .92
    assert selected['version'] == original['version'] and new['buyer_counter_ratio'] == .96
    assert len(j.events('buyer_order')) == 2


def test_public_hype_and_fresh_or_ambiguous_orders_do_not_trigger_change(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j, start=NOW - 100)
    assert evidence(j, 'buyer', 0, NOW)['samples'] == 0
    update(j, NOW)
    assert j.get(KEY + 'buyer') is None
    assert not j.events('strategy_change')
    assert update(j, NOW)['status'] == 'cooldown'


def test_trial_starts_once_and_marks_auditable_version(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _: update(j, NOW), range(3)))
    assert sum(r['status'] == 'evaluated' for r in results) == 1
    state = j.get(KEY + 'buyer')
    assert state['status'] == 'trial' and state['patch'] == {'buyer_counter_ratio': .94}
    assert state['version'] == 1 and len(j.events('strategy_change')) == 1


@pytest.mark.parametrize('improves', [True, False])
def test_trial_promotes_or_rolls_back_from_own_and_peer_evidence(tmp_path, improves):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    update(j, NOW)
    buyer_events(j, version=1, start=NOW + 100, paid=10)
    later = NOW + MATURITY + 1000
    board(j, later, buyer=20000 if improves else 0, peers=1200)
    update(j, later)
    state = j.get(KEY + 'buyer')
    assert state['status'] == 'stable' and 'trial' not in state
    assert state['version'] == (1 if improves else 2)
    assert state['patch'] == ({'buyer_counter_ratio': .94} if improves else {})
    assert j.events('strategy_change')[0]['action'] == ('promote' if improves else 'rollback')


def test_better_board_without_successful_own_trial_cannot_promote(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    update(j, NOW)
    buyer_events(j, version=1, start=NOW + 100, paid=0)
    later = NOW + MATURITY + 1000
    board(j, later, buyer=20000, peers=1200)
    update(j, later)
    assert j.get(KEY + 'buyer')['status'] == 'trial'


def test_pending_expected_refund_is_not_final_evidence(tmp_path):
    j = Journal(tmp_path)
    buyer_events(j, paid=20)
    j.add('outcome', {'order': 'v0-o0', 'broken': True,
                     'refund_status': 'expected_by_warranty_rule'}, ts=NOW)
    assert evidence(j, 'buyer', 0, NOW)['samples'] == 19


def test_shop_category_allocation_requires_own_and_public_sales_and_affects_restock(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    cat = {'G1': {'category': 'x', 'list_price': 10000, 'kind': 'goods'}}
    atomic_json(j.md / 'catalog.json', cat)
    atomic_json(j.md / 'inventory.json', {'round': 3, 'balance': 5000000, 'units': [{}] * 30})
    for n in range(20):
        j.add('shop_opportunity', {'task': str(n), 'strategy_version': 0}, ts=NOW - MATURITY - 100)
        if n < 10:
            j.add('shop_settled', {'task': str(n), 'category': 'x', 'paid_at': NOW - MATURITY - 90},
                  ts=NOW - MATURITY - 90)
    for n in range(5):
        j.add('public_settle', {'sku': 'G1', 'shop': 'https://other.example'}, ts=NOW - 100)
    assert public_context(j, NOW) == {'x': 5}
    update(j, NOW)
    cfg, _ = effective(j, 'shop')
    assert cfg['category_weights'] == {'x': 1.1}
    candidates = restock_candidates({'balance': 5000000, 'units': []}, cat, [], j, cfg, now=NOW)
    assert candidates[0]['evidence']['adaptive_category_weight'] == 1.1
    assert candidates[0]['max_cost'] <= 5000000 * .8


def test_round_reset_or_stale_board_cannot_update(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    j.put('board_round', 2)
    assert update(j, NOW)['status'] == 'waiting_for_fresh_round3_board'
    j.put('board_round', 3)
    j.put('board_checked_at', NOW - 301)
    assert update(j, NOW)['status'] == 'waiting_for_fresh_round3_board'
    assert not j.events('strategy_change')


@pytest.mark.parametrize('stock,paid,target', [(2, 12, 65), (60, 0, 55)])
def test_inventory_target_changes_with_own_stock_and_conversion(tmp_path, stock, paid, target):
    j = Journal(tmp_path)
    baseline(j)
    atomic_json(j.md / 'inventory.json', {'round': 3, 'balance': 5000000, 'units': [{}] * stock})
    atomic_json(j.md / 'catalog.json', {'G1': {'category': 'x'}})
    for n in range(20):
        j.add('shop_opportunity', {'task': str(n), 'strategy_version': 0}, ts=NOW - MATURITY - 100)
        if n < paid:
            j.add('shop_settled', {'task': str(n), 'paid_at': NOW - MATURITY - 90}, ts=NOW - MATURITY - 90)
    for n in range(5):
        j.add('public_settle', {'sku': 'G1'}, ts=NOW - 100)
    update(j, NOW)
    cfg, _ = effective(j, 'shop')
    assert cfg['inventory_target_units'] == target
    assert cfg['cash_reserve_fraction'] == .2
    assert cfg['restock_max_lines'] == policy()['restock_max_lines']


def test_trial_without_enough_new_evidence_expires_and_restores_baseline(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    update(j, NOW)
    later = NOW + 48 * 3600
    board(j, later, buyer=1000000)
    update(j, later)
    state = j.get(KEY + 'buyer')
    assert state['status'] == 'stable' and state['patch'] == {} and state['version'] == 2
    assert j.events('strategy_change')[0]['action'] == 'rollback'


def test_rejected_strategy_can_be_reconsidered_after_cooldown(tmp_path):
    j = Journal(tmp_path)
    baseline(j)
    buyer_events(j)
    j.put(KEY + 'buyer', {'version': 0, 'patch': {}, 'status': 'stable',
                          'rejected': [{'patch': identity({'buyer_counter_ratio': .94}), 'until': NOW + 1000}]})
    update(j, NOW)
    assert j.get(KEY + 'buyer')['status'] == 'stable'
    board(j, NOW + 3600)
    update(j, NOW + 3600)
    assert j.get(KEY + 'buyer')['status'] == 'trial'
