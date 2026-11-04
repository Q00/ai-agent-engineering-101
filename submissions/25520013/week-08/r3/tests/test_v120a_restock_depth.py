"""v1.20a: mattress stock depth. G1515d951 got 0 units in Round 3 because skus_per_category was unset (1):
only the top SKU by settles (G07ec17d5) was ever planned. Caps (balance floor, inventory cap, max_unit_cost,
below-market filter) must still hold."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import policy as P  # noqa: E402

NOW = 1793708000.0
M = '가구/침구>가구>침대/매트리스'
FOODS = ['식품>테스트>분류%d' % i for i in range(6)]


class Stats:
    def __init__(self, sku=None, cat=None):
        self._sku, self._cat = sku or {}, cat or {}

    def sku(self, k):
        return self._sku.get(k)

    def category(self, k):
        return self._cat.get(k)


def _world(extra_sku=None):
    catalog = {
        'G07ec17d5': {'sku': 'G07ec17d5', 'category': M, 'kind': 'goods', 'list_price': 279000},
        'G1515d951': {'sku': 'G1515d951', 'category': M, 'kind': 'goods', 'list_price': 423810},
        'Gc21ca0bf': {'sku': 'Gc21ca0bf', 'category': M, 'kind': 'goods', 'list_price': 59480},
    }
    sku = {'G07ec17d5': {'settles_all': 1337}, 'G1515d951': {'settles_all': 1170}, 'Gc21ca0bf': {'settles_all': 211}}
    cat = {M: {'velocity_per_h': 100.0}}
    demand = [{'category': M, 'kind': 'goods', 'median_price': 250000}]
    for i, f in enumerate(FOODS):
        s = 'F%02d' % i
        catalog[s] = {'sku': s, 'category': f, 'kind': 'food', 'list_price': 20000, 'shelf_hours': 168}
        cat[f] = {'velocity_per_h': 500.0 - i}
        demand.append({'category': f, 'kind': 'food', 'median_price': 20000})
    if extra_sku:
        catalog.update(extra_sku[0]); sku.update(extra_sku[1])
    return catalog, demand, Stats(sku, cat)


def _inv(balance=10_900_000, units=None):
    return {'balance': balance, 'units': units or [], 'storage_per_day': 0.01,
            'restock': {'open': True, 'opens_at': NOW - 10, 'closes_at': NOW + 170}}


def _plan(pol=None, inv=None, world=None):
    catalog, demand, stats = world or _world()
    return P.restock_plan(inv or _inv(), demand, catalog, pol or P.load(), stats, activity={}, now=NOW)


def _qty(buy):
    return {b['sku']: b['qty'] for b in buy}


def test_policy_values_v120a():
    pol = P.load()
    sp = pol['shop']
    assert sp['max_spend_per_run'] == 1_100_000 and sp['restock_priority'] == [M]
    assert sp['balance_floor'] == 3_000_000 and sp['max_inventory_cost_frac'] == 0.3
    assert sp['max_total_units'] == 250 and sp['max_unit_cost'] == 400_000
    c = P.resolve(pol, M, 'goods')
    assert (c['stock_target'], c['qty_per_buy'], c['breadth_qty'], c['skus_per_category']) == (8, 4, 2, 2)
    f = P.resolve(pol, FOODS[0], 'food')
    assert 'skus_per_category' not in f and f['stock_target'] == 5


def test_root_cause_reproduced_without_v120a():
    pol = P.load()
    pol['categories'].pop(M)
    pol['shop'].pop('restock_priority')
    pol['shop']['max_spend_per_run'] = 600_000
    q = _qty(_plan(pol)[0])
    assert 'G1515d951' not in q
    assert q.get('G07ec17d5', 0) >= 1


def test_g1515d951_is_planned_with_g07ec17d5():
    buy, why = _plan()
    q = _qty(buy)
    assert q.get('G07ec17d5') == 2 and q.get('G1515d951') == 2
    assert 'Gc21ca0bf' not in q
    assert [b['sku'] for b in buy][:2] == ['G07ec17d5', 'G1515d951']


def test_priority_beats_higher_velocity():
    buy, _ = _plan()
    assert buy[0]['sku'] == 'G07ec17d5'


def test_priority_by_leaf_name():
    pol = P.load()
    pol['shop']['restock_priority'] = ['침대/매트리스']
    buy, _ = _plan(pol)
    assert buy[0]['sku'] == 'G07ec17d5'


def test_worst_spend_within_budget_and_cap():
    buy, why = _plan()
    assert why['worst_spend'] <= why['budget'] <= 1_100_000


def test_inventory_cap_still_binds():
    units = [{'category': 'x', 'kind': 'goods', 'cost': 1_200_000, 'list_price': 2_000_000, 'grade': 'A'}]
    buy, why = _plan(inv=_inv(balance=5_000_000, units=units))
    assert why.get('budget', 0) <= int(0.3 * 5_000_000) - 1_200_000
    assert why.get('worst_spend', 0) <= why.get('budget', 0)


def test_balance_floor_still_binds():
    buy, why = _plan(inv=_inv(balance=3_400_000))
    assert why.get('budget', 0) <= 400_000
    assert why.get('worst_spend', 0) <= 400_000


def test_below_balance_floor_buys_nothing():
    buy, why = _plan(inv=_inv(balance=2_900_000))
    assert buy == []


def test_max_unit_cost_still_filters():
    big = ({'Gbig00001': {'sku': 'Gbig00001', 'category': M, 'kind': 'goods', 'list_price': 600000}},
           {'Gbig00001': {'settles_all': 9999}})
    q = _qty(_plan(world=_world(big))[0])
    assert 'Gbig00001' not in q and q.get('G07ec17d5') == 2 and q.get('G1515d951') == 2


def test_below_market_filter_still_skips():
    catalog, demand, stats = _world()
    stats._sku['G1515d951'] = {'settles_all': 1170, 'n': 50, 'p50': 100000}
    q = _qty(_plan(world=(catalog, demand, stats))[0])
    assert 'G1515d951' not in q and q.get('G07ec17d5') == 2 and q.get('Gc21ca0bf') == 2


def test_stock_target_counts_units_on_hand():
    units = [{'category': M, 'kind': 'goods', 'cost': 150000, 'list_price': 279000, 'grade': 'A'} for _ in range(6)]
    q = _qty(_plan(inv=_inv(units=units))[0])
    assert q.get('G07ec17d5', 0) + q.get('G1515d951', 0) == 2


def test_full_category_buys_no_mattress():
    units = [{'category': M, 'kind': 'goods', 'cost': 150000, 'list_price': 279000, 'grade': 'A'} for _ in range(8)]
    q = _qty(_plan(inv=_inv(units=units))[0])
    assert not set(q) & {'G07ec17d5', 'G1515d951', 'Gc21ca0bf'}


@pytest.mark.parametrize('bad', [0, 6, True, '2', 1.5])
def test_validate_rejects_bad_skus_per_category(bad):
    pol = P.load()
    pol['categories'][M]['skus_per_category'] = bad
    with pytest.raises(P.PolicyError):
        P.validate(pol)


@pytest.mark.parametrize('bad', ['침대/매트리스', [1], [''], {'a': 1}])
def test_validate_rejects_bad_restock_priority(bad):
    pol = P.load()
    pol['shop']['restock_priority'] = bad
    with pytest.raises(P.PolicyError):
        P.validate(pol)


def test_policy_json_round_trips_in_file_format():
    import json
    raw = (Path(__file__).resolve().parents[1] / 'policy.json').read_text(encoding='utf-8')
    assert json.dumps(json.loads(raw), indent=1, ensure_ascii=False) + chr(10) == raw
