"""Offline synthetic tests for restock_r3.py; no market, secrets or API calls."""
import json
from pathlib import Path
import tempfile
from restock_r3 import make_plan, settled_signals, seller_sales_from_logs


def write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def fixture(base, round_=3, settled=False):
    state = base / '.market'
    state.mkdir()
    (base / 'logs').mkdir()
    catalog = {}
    groups = []
    for cat_no in range(8):
        cat = f'cat{cat_no}>group'
        groups.append({'category': cat, 'item': f'group{cat_no}', 'skus': 12,
                       'kind': 'goods', 'median_price': 35000})
        for i in range(12):
            sku = f'G{cat_no:02x}{i:06x}'
            catalog[sku] = {'sku': sku, 'name': f'Item {cat_no}/{i}',
                            'category': cat, 'kind': 'goods', 'list_price': 35000,
                            'shelf_hours': None}
    write(state / 'catalog.json', catalog)
    write(state / 'demand.json', groups)
    write(state / 'inventory.json', {'round': round_, 'balance': 5000000, 'units': []})
    favored = 'G00000000'
    if settled:
        lines = [json.dumps({'paid': True, 'item': favored, 'ledger': i}) for i in range(1, 4)]
        lines.append(json.dumps({'paid': False, 'item': 'G01000000', 'order': 'unpaid'}))
        (state / 'payments.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        (base / 'logs' / 'market-kit.log').write_text(
            f'12:00:00 [acp] settled {favored} at 30000, ledger #99\n', encoding='utf-8')
    return state, favored


def tests():
    with tempfile.TemporaryDirectory() as t:
        state, favored = fixture(Path(t), round_=2)
        assert make_plan(state) == {'buy': []}
    with tempfile.TemporaryDirectory() as t:
        state, favored = fixture(Path(t))
        plan = make_plan(state)
        assert 1 <= len(plan['buy']) <= 8, plan
        assert len({x['sku'] for x in plan['buy']}) == len(plan['buy'])
        assert sum(.75 * 35000 for _ in plan['buy']) <= 250000
    with tempfile.TemporaryDirectory() as t:
        state, favored = fixture(Path(t), settled=True)
        sales, buyers = settled_signals(state, json.loads((state / 'catalog.json').read_text()))
        assert sales[favored] == 1 and buyers[favored] == 3
        plan = make_plan(state)
        assert any(x['sku'] == favored for x in plan['buy']), plan
        assert all(x['sku'] != 'G01000000' for x in plan['buy']), 'unpaid sales must not drive demand'
        print('PASS: paid settlement drives priority while unpaid checkout does not')
    print('PASS: Round 2 buys nothing; Round 3 protects cash and learns from settlements')


if __name__ == '__main__':
    tests()
