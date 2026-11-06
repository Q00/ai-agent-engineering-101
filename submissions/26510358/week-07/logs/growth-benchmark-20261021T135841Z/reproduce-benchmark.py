"""Offline Python scratchpad: rerun saved official API snapshots without trading.

Run from any working directory. Only writes a new derived JSON beside this file.
Visits are market counters, not unique people. Recent review windows and the
last 500 ledger events are samples; none estimates a causal growth effect.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean, median

HERE = Path(__file__).resolve().parent
NAMES = ('lee-sungsoo', '11im', 'myeonggi')


def calculate():
    read = lambda name: json.loads((HERE / name).read_text())
    board, registry, ledger = read('board.json'), read('shops.json'), read('ledger.json')
    assert board['current'] == registry['round'] == 2
    ordered = sorted(board['shops'], key=lambda row: row['deals'], reverse=True)
    metrics = []
    for rank, row in enumerate(ordered, 1):
        if row['name'] in NAMES:
            metrics.append({'shop': row['name'], 'rank': rank, 'visits': row['visits'],
                            'deals': row['deals'], 'deal_visit_ratio': row['deals']/row['visits'],
                            'stars': row['stars']})
    urls = {row['url'].rstrip('/'): row['name'] for row in registry['shops'] if row['name'] in NAMES}
    prices, matched = defaultdict(list), []
    for row in ledger:
        data = row['data']
        name = urls.get(str(data.get('shop', '')).rstrip('/'))
        if row['round'] == 2 and row['kind'] == 'result' and data.get('verified') is True and name:
            receipt = data['receipt']
            prices[name, receipt['item']].append(receipt['price'])
            matched.append({'seq': row['seq'], 'shop': name, 'item': receipt['item'],
                            'price': receipt['price'], 'buyer': data['buyer'], 'stars': data['stars']})
    summaries = [{'shop': name, 'item': item, 'n': len(values), 'min': min(values),
                  'median': median(values), 'max': max(values)}
                 for (name, item), values in sorted(prices.items())]
    reviews = {}
    for name in NAMES:
        rows = read(name + '-reviews.json')
        reviews[name] = {'n': len(rows), 'mean': mean(row['stars'] for row in rows),
                         'author_counts': dict(Counter(row['author'] for row in rows)),
                         'own_quality_notes': [{key: row[key] for key in ('author','stars','review','ts')}
                                               for row in rows[:4]]}
    result = {'round': 2, 'board_metrics': metrics,
              'ledger_window': {'first_seq': ledger[0]['seq'], 'last_seq': ledger[-1]['seq'], 'rows': len(ledger)},
              'verified_prices': summaries, 'verified_rows': matched, 'reviews': reviews}
    # Reconcile every published calculation against the earlier captured result.
    assert result == read('benchmark-calculations.json')
    return result


if __name__ == '__main__':
    result = calculate()
    (HERE / 'benchmark-reproduced.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'matched_saved_calculations': True, 'board_metrics': result['board_metrics'],
                      'verified_price_samples': result['verified_prices'],
                      'own_recent_review_authors': result['reviews']['myeonggi']['author_counts']},
                     ensure_ascii=False, indent=2))
