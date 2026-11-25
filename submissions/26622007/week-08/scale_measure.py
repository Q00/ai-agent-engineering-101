"""Read-only unit joins for Terra's scale decision; departures are not paid sales."""
import os
import time

from profit_cohorts import read_json, read_rows


def summarize(windows, incoming, outgoing, ledger, mine, targets, now):
    windows = sorted({w['opens_at']: w for w in windows
                      if w.get('ts', now + 1) <= now and w.get('closes_at', now + 1) <= now
                      and isinstance(w.get('opens_at'), (int, float))}.values(),
                     key=lambda w: w['opens_at'])[-3:]
    result = {'as_of': now, 'windows': [w['opens_at'] for w in windows], 'skus': {},
              'basis': 'Paid ledger proves sales; earlier paid/observed departure proves retention only. '
                       'No cap increase is authorized by this measurement alone.'}
    if len(windows) < 3:
        result['missing'] = 'three completed, observed supply windows'
        return result
    first, middle, last = [w['opens_at'] for w in windows]
    arrivals = {}
    for row in incoming:
        if row.get('ts', now + 1) <= now and row.get('unit'):
            arrivals.setdefault(row['unit'], row)
    departures, paid = {}, {}
    for row in outgoing:
        if row.get('ts', now + 1) <= now and row.get('unit'):
            u = row['unit']
            departures[u] = min(departures.get(u, now + 1), row['ts'])
    seen = set()
    for row in ledger:
        if row.get('k') != 'settle' or row.get('ts', now + 1) > now or row.get('seq') in seen:
            continue
        seen.add(row.get('seq'))
        unit = mine.get(str(row.get('seq')), {}).get('unit')
        if unit:
            paid[unit] = min(paid.get(unit, now + 1), row['ts'])
            departures[unit] = min(departures.get(unit, now + 1), row['ts'])
    for sku, target in targets.items():
        units = {u: r for u, r in arrivals.items() if r.get('sku') == sku}
        # first_tick is pre-existing stock, not proof of a purchase in this window.
        bought = [u for u, r in units.items() if not r.get('first_tick')
                  and first <= r.get('supplied_at', r['ts']) < middle]
        sold = [u for u in bought if paid.get(u, now + 1) <= last]
        held = [u for u, r in units.items() if r.get('supplied_at', r['ts']) <= middle
                and departures.get(u, now + 1) > last]
        result['skus'][sku] = {'target': target, 'bought_two_windows_ago': bought,
                               'paid_by_last_open': sold,
                               'paid_fraction': len(sold) / len(bought) if bought else None,
                               'same_units_across_last_two_opens': held,
                               'extra_retained': len(held) >= target if target else None}
    return result


def build(root, now=None):
    now = time.time() if now is None else now
    md = os.path.join(root, '.market')
    cfg = read_json(os.path.join(root, 'pricing.json'), {}).get('restock', {})
    cat = read_json(os.path.join(md, 'catalog.json'), {})
    targets = {s: n for s, n in cfg.get('sku_target_units', {}).items()
               if cat.get(s, {}).get('list_price', 0) >= 600000}
    rows = lambda name: read_rows(os.path.join(md, name))
    return summarize(rows('measure/supply_windows.jsonl'), rows('measure/units_in.jsonl'),
                     rows('measure/units_out.jsonl'), rows('ledger_rows.jsonl'),
                     read_json(os.path.join(md, 'loss_state.json'), {}).get('mine', {}), targets, now)
