import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r3.state import Journal, policy, atomic_json
from r3.merchant import observe_inventory, context
from r3.economy import restock_candidates, cash_capacity


def config():
    return dict(policy(), shop_growth_v5=True, shop_velocity_enabled=False,inventory_target_units=80, restock_max_qty=20,
                assortment_coverage_fraction=.35)


def test_available_time_forecast_does_not_treat_stockout_time_as_low_demand(tmp_path):
    j, now = Journal(tmp_path), time.time()
    cat = {'G1': {'kind': 'food', 'list_price': 20000, 'category': 'food', 'shelf_hours': 48}}
    # Six requests in four minutes of verified availability; the remaining
    # hour was out of stock. Do not divide the requests by the whole hour.
    history = [{'at': now-3600+x*30, 'counts': {'G1': 1}} for x in range(9)]
    history.append({'at': now-3330, 'counts': {}})
    j.put('merchant_stock_timeline', history)
    for n in range(6):
        ts = now-3590+n*30
        j.add('shop_opportunity', {'task': str(n), 'sku': 'G1'}, ts=ts)
        j.add('customer_visit', {'task': str(n), 'sku': 'G1', 'category': 'food'}, ts=ts)
        if n<4:
            j.add('shop_settled', {'task':str(n),'sku':'G1','price':16000,'cost':10000,'paid_at':ts+2},ts=ts+2)
    for n in range(60):
        j.add('public_settle', {'sku': 'G1', 'amount': 16000, 'shop': 'peer'}, ts=now-n)
    rows = context(j, cat, now)['skus']['G1']
    assert rows['available_requests'] == 6 and .07 < rows['available_hours_observed'] < .08
    assert rows['arrival_rate_scenario'] > 10 and rows['payment_rate_scenario']==5/8
    cs = restock_candidates({'balance': 5000000, 'units': []}, cat, [], j, config(), now)
    assert cs and cs[0]['qty'] >= 6
    assert sum(c['capital_commitment'] for c in cs) <= cash_capacity({'balance': 5000000}, j, config(), now)['spendable']


def test_exposure_gaps_and_fifo_cannot_invent_uptime(tmp_path):
    j, now = Journal(tmp_path), time.time()
    cat = {'G1': {'kind': 'goods', 'list_price': 10000, 'category': 'x'}}
    j.put('merchant_stock_timeline', [{'at': now-7200, 'counts': {'G1': 1}}])
    j.add('shop_opportunity', {'task': 'gap', 'sku': 'G1'}, ts=now-1000)
    j.add('customer_visit', {'task': 'gap', 'sku': 'G1'}, ts=now-1000)
    row = context(j, cat, now)['skus'].get('G1', {})
    assert row.get('available_requests', 0) == 0
    for n in range(1100):
        observe_inventory(j, {'round': 3, 'units': [{'sku': 'G1'}] if n%2 else []}, now+n)
    assert len(j.get('merchant_stock_timeline')) == 1024


def test_basket_coverage_respects_budget_and_does_not_use_private_budgets(tmp_path):
    j, now = Journal(tmp_path), time.time()
    cat = {'G1': {'kind': 'food', 'list_price': 20000, 'category': 'rice', 'shelf_hours': 48},
           'G2': {'kind': 'food', 'list_price': 25000, 'category': 'milk', 'shelf_hours': 48}}
    j.add('buyer_order', {'order': 'private', 'acceptable': ['G2'], 'budget': 987654321}, ts=now)
    for sku in cat:
        for n in range(30): j.add('public_settle', {'sku': sku, 'amount': cat[sku]['list_price']*.8,
                                                  'shop': 'peer'}, ts=now-n)
    m = context(j, cat, now)
    assert any(k.startswith('own-basket:') for k in m['skus']['G2']['coverage_keys'])
    assert '987654321' not in str(m)
    inv = {'balance': 5000000, 'units': []}
    cs = restock_candidates(inv, cat, [], j, config(), now)
    assert {'G1', 'G2'} <= {c['sku'] for c in cs}
    assert all(c['allocation_book'] == 'coverage_and_depth' for c in cs)
    assert sum(c['capital_commitment'] for c in cs) <= cash_capacity(inv, j, config(), now)['spendable']
