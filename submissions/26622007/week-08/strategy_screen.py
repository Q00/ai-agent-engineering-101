"""Read-only public-demand screen; prices are evidence, rival costs/margins are unknown."""
import math
import statistics
import time
from pathlib import Path
import profit_cohorts
import profit_price


def screen(rows, catalog, inventory, pricing, now, hours=2, limit=5, sku=None):
    hours = max(1, min(12, hours))
    grouped = {}
    seen = set()
    for row in rows:
        key = row.get('sku')
        if row.get('k') != 'settle' or row.get('seq') in seen or not now-hours*3600 <= row.get('ts', 0) <= now:
            continue
        seen.add(row.get('seq'))
        if sku and key != sku:
            continue
        item = catalog.get(key) or {}
        price = row.get('amount')
        if item.get('kind') != 'goods' or not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
            continue
        grouped.setdefault(key, []).append(row)
    surge = {r.get('sku'): r.get('factor') for r in inventory.get('surge', []) if isinstance(r, dict)}
    result = []
    for key, sales in grouped.items():
        lp = catalog[key].get('list_price')
        if not isinstance(lp, (int, float)) or isinstance(lp, bool) or not math.isfinite(lp) or lp <= 0:
            continue
        factor = surge.get(key, 1.0)
        if not isinstance(factor, (int, float)) or isinstance(factor, bool) or not math.isfinite(factor) or not 1 <= factor <= 1.5:
            continue
        cost = math.ceil(lp * .75 * factor)
        floor = profit_price.floor(cost, lp, 'goods', .15, profit_price.cfg_of(pricing))
        stress = profit_price.floor(cost, lp, 'goods', .25, profit_price.cfg_of(pricing))
        prices = [s['amount'] for s in sales]
        result.append(dict(sku=key, paid=len(sales), buyers=len({s.get('buyer') for s in sales}),
                           price_min=min(prices), price_median=statistics.median(prices), price_max=max(prices),
                           list_price=lp, surge=factor, worst_unit_cost=cost, floor15=floor, floor25=stress,
                           clears_floor15=sum(p >= floor for p in prices), clears_floor25=sum(p >= stress for p in prices),
                           stock_units=sum(u.get('sku') == key for u in inventory.get('units', []))))
    result.sort(key=lambda r: (-r['clears_floor25'], -r['paid'], r['sku']))
    latest = max((r.get('ts', 0) for r in rows if r.get('ts', 0) <= now), default=0)
    return dict(as_of=now, hours=hours, ledger_lag_seconds=now-latest if latest else None,
                basis='public paid demand; hypothetical worst A cost incl snapshot surge; b=15%/25% scenarios, not measured risk or rival margin; no automatic buy',
                candidates=result[:max(1, min(20, limit))])


def build(root, now=None, sku=None):
    root = Path(root)
    md = root / '.market'
    return screen(profit_cohorts.read_rows(md/'ledger_rows.jsonl'),
                  profit_cohorts.read_json(md/'catalog.json', {}),
                  profit_cohorts.read_json(md/'inventory.json', {}),
                  profit_cohorts.read_json(root/'pricing.json', {}),
                  time.time() if now is None else now, sku=sku)
