"""Read-only identity evidence over two completed supply windows, never a buy decision."""
import json
import os
import sys
import time
from pathlib import Path


def read_rows(path):
    try:
        return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return None


def audit(windows, incoming, outgoing, now, sku):
    if any(x is None for x in (windows, incoming, outgoing)):
        return {'available': False, 'reason': 'missing or malformed measurements', 'sku': sku}
    windows = sorted({w['opens_at']: w for w in windows
                      if isinstance(w.get('opens_at'), (int, float))
                      and isinstance(w.get('closes_at'), (int, float))
                      and w['closes_at'] <= now and w.get('ts', now) <= now}.values(),
                     key=lambda w: w['opens_at'])[-2:]
    if len(windows) < 2:
        return {'available': False, 'reason': 'need two completed windows', 'sku': sku}
    first, second = windows
    entered = {r['unit']: r for r in incoming if r.get('sku') == sku and r.get('unit')
               and r.get('ts', now + 1) <= now and not r.get('first_tick')
               and first['opens_at'] <= (r.get('supplied_at') or r.get('ts', 0)) <= first['closes_at']}
    exits = {r['unit']: r for r in outgoing if r.get('unit') and r.get('ts', now + 1) <= second['closes_at']}
    sold = sorted(u for u in entered if u in exits and exits[u].get('how') == 'sold')
    remaining = sorted(u for u in entered if u not in exits)
    other = sorted(u for u in entered if u in exits and exits[u].get('how') != 'sold')
    return {'available': True, 'sku': sku, 'windows': [w['opens_at'] for w in windows],
            'bought_first_window': len(entered), 'sold_by_second_close': len(sold),
            'sold_fraction': len(sold) / len(entered) if entered else None,
            'remaining_units': remaining, 'sold_units': sold, 'other_exits': other,
            'window_lines': [[x for x in w.get('lines', []) if x.get('sku') == sku] for w in windows],
            'basis': 'observed unit IDs; non-sale exits are not sales; remaining is not necessarily extra stock',
            'requires': ['guard cut reason and binding target', 'mature cohort net > 0', 'book >= 6h ago',
                         'complete measurement coverage; absence of unit events is not proof of zero activity']}


def main():
    args=json.load(sys.stdin); root=Path(os.environ['MARKET_DIR'])/'measure'
    print(json.dumps(audit(*(read_rows(root / (n+'.jsonl')) for n in ('supply_windows','units_in','units_out')),
                           time.time(), str(args.get('sku',''))),ensure_ascii=False))

if __name__ == '__main__':
    main()
