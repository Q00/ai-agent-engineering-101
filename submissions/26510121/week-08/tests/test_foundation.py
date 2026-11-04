import concurrent.futures
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3.state import Journal, policy
from r3.economy import buyer_value, expected_surplus, grade, restock_candidates, shop_candidates
from r3.model import Chooser, ModelError


def test_duplicate_events_do_not_double_reward(tmp_path):
    j = Journal(tmp_path)
    assert j.add('purchase', {'order': 'x'}, eid='order:x')
    assert not j.add('purchase', {'order': 'x'}, eid='order:x')
    assert len(j.events('purchase')) == 1


def test_reservation_exclusion_across_process_connections(tmp_path):
    j = Journal(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda n: j.reserve('one', str(n), now=100), range(8)))
    assert sum(results) == 1
    assert j.reserve('one', 'next', now=1000)


def test_cost_cap_is_atomic(tmp_path):
    j = Journal(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(5) as pool:
        tickets = list(pool.map(lambda _: j.hold_budget(.4, 1), range(5)))
    assert sum(x is not None for x in tickets) == 2


def test_value_is_not_budget_and_bad_food_is_negative():
    product = {'list_price': 1000, 'kind': 'food'}
    assert buyer_value(product, 500, hours_left=2, use_hours=3)['surplus'] == -500
    assert buyer_value(product, 500, hours_left=3, use_hours=3)['surplus'] == 500
    assert buyer_value({'list_price': 1000, 'kind': 'goods'}, 500, broken=True, refund=500)['surplus'] == 0
    assert expected_surplus(1000, 500, .2, .2) == 400


def test_food_grade_changes_without_rewriting_inventory():
    u = {'sku': 'a', 'kind': 'food', 'expires_at': 36000, 'grade': 'A'}
    cat = {'a': {'shelf_hours': 10}}
    assert grade(u, cat, now=0) == 'A'
    assert grade(u, cat, now=18000) == 'B'
    assert grade(u, cat, now=35000) == 'C'
    assert grade(u, cat, now=36000) == 'expired'
    assert u['grade'] == 'A'


def test_already_owned_expiring_stock_can_be_sold_below_cost(tmp_path):
    now = time.time()
    u = {'unit': 'u', 'sku': 'a', 'kind': 'food', 'expires_at': now + 600, 'list_price': 10000, 'cost': 6000}
    cs = shop_candidates(u, {}, {'a': {'shelf_hours': 24}}, Journal(tmp_path), policy(), offer=3000, now=now)
    assert any(c['action'] == 'accept' and c['price'] == 3000 for c in cs)


def test_restock_bounds_and_zero_capital(tmp_path):
    j = Journal(tmp_path)
    cat = {'a': {'category': 'x', 'list_price': 10000, 'kind': 'goods'}}
    assert not restock_candidates({'balance': 0}, cat, [], j, policy())
    cs = restock_candidates({'balance': 5000000}, cat, [], j, policy())
    assert len(cs) == 1 and cs[0]['qty'] == 1 and cs[0]['max_cost'] == 7500


def test_budget_and_transport_failure_do_not_leak_keys(tmp_path, monkeypatch):
    import r3.model as model
    monkeypatch.setattr(model, 'api_key', lambda: 'PRIVATE_TEST_VALUE')
    class BrokenClient:
        def post(self, *args, **kwargs):
            class Response:
                status_code = 401
            return Response()
    j = Journal(tmp_path)
    c = Chooser(j, client=BrokenClient())
    chosen = c.choose('buyer', [{'id': 'a', 'score': 1}, {'id': 'b', 'score': .9}], {})
    assert chosen['id'] == 'a'
    assert 'PRIVATE_TEST_VALUE' not in json.dumps(j.events('decision'))
    assert j.events('decision')[0]['mode'] == 'numeric_fallback'


def test_incomplete_stream_record_is_not_lost(tmp_path):
    j = Journal(tmp_path)
    p = tmp_path / 'payments.jsonl'
    p.write_text('{"order":"o","paid":true}', encoding='utf-8')
    j.reconcile()
    assert not j.events('payment')
    with p.open('a') as f:
        f.write('\n')
    j.reconcile()
    j.reconcile()
    assert len(j.events('payment')) == 1


def test_ambiguous_break_is_not_assigned_to_a_guessed_seller(tmp_path):
    j = Journal(tmp_path)
    for o in ['o1', 'o2']:
        j.add('purchase', {'order': o, 'name': 'same', 'paid_at': 1000, 'price': 10, 'value': 20, 'kind': 'goods'})
    j.add('broken_news', {'name': 'same', 'broke_at': 2000})
    j.reconcile(now=3000)
    assert j.events('ambiguous_break')
    assert not j.events('outcome')


def test_null_warranty_does_not_poison_reconciliation_after_break(tmp_path):
    j = Journal(tmp_path)
    j.add('purchase', {'order': 'null-warranty', 'name': 'single product', 'kind': 'goods',
                       'paid_at': 1000, 'price': 100, 'value': 200, 'warranty_hours': None})
    j.add('broken_news', {'name': 'single product', 'broke_at': 1200})
    j.reconcile(now=30000)
    j.reconcile(now=30000)
    result = j.events('outcome')
    assert len(result) == 1 and result[0]['broken'] and not result[0]['covered']
    assert result[0]['surplus'] == -100 and result[0]['refund_status'] == 'not_covered'
    assert j.events('purchase')[0]['warranty_hours'] is None  # Preserve raw historical evidence.


def test_cash_capacity_accepts_null_warranty_but_keeps_real_liability(tmp_path):
    from r3.economy import cash_capacity
    j = Journal(tmp_path)
    now = time.time()
    j.add('shop_settled', {'price': 100, 'paid_at': now, 'warranty_hours': None})
    j.add('shop_settled', {'price': 200, 'paid_at': now, 'warranty_hours': 6})
    assert cash_capacity({'balance': 5000000, 'units': []}, j, policy(), now)['refund_liability'] == 200


def test_review_normalizes_nullable_warranty(tmp_path):
    from review import review
    j = Journal(tmp_path)
    review({'order': {'order': 'null'}, 'receipt': {'price': 10},
            'settled': {'sku': 'G1', 'shop': 'shop', 'ledger': 1, 'value': 20, 'warranty_hours': None}}, j)
    assert j.events('purchase')[0]['warranty_hours'] == 0
