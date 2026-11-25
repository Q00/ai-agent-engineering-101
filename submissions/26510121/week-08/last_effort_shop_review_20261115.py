"""Read-only decision review; never imports a policy writer or market client."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent
BASE = 'logs/autonomous-deploy-before-20261114T055614486450Z.json'
SOURCE = 'logs/v28-progress-source-20261115T073129572389Z.json'
DIAG = 'logs/progress-diagnosis-20261115T073634434694Z.json'
V281 = 'logs/autonomous-deploy-before-20261115T024506256434Z.json'
MORNING = 'logs/v28-progress-source-20261115T022256766670Z.json'


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def main():
    base, source, diag = read(BASE), read(SOURCE), read(DIAG)
    lo, hi = base['at'], source['responses']['/api/board']['finished_at']
    v281_at = read(V281)['at']
    board = source['responses']['/api/board']['data']
    own = next(r for r in board['shops'] if r['name'] == 'seochanit')
    old_own = next(r for r in base['board']['shops'] if r['name'] == 'seochanit')
    inventory = base['inventory']['units']
    goods_cost = sum(u['cost'] for u in inventory if u['kind'] == 'goods')
    expired_if_idle = [u for u in inventory if u['kind'] == 'food' and u.get('expires_at') and u['expires_at'] <= hi]
    cat = read('.market/catalog.json')
    report = {'sources': [BASE, SOURCE, DIAG, V281, MORNING], 'cutoff': hi,
              'window': diag['window'], 'official': diag['own']['shops'],
              'book_bridge': diag['bridge'], 'thresholds': diag['next_thresholds']['shops'],
              'review_quality': diag['shop']['review_quality_by_sold_grade'],
              'review_reasons': diag['shop']['low_review_reasons']}
    from r3.meta import eligible_rows, percentile
    cohort = eligible_rows(board, 'shop', 'seochanit')
    ratings = [r['stars_rank'] for r in cohort if type(r.get('stars_rank')) in (int, float)]
    report['fixed_cohort_stars_scenarios'] = {
        'cohort_n': len(cohort),
        'scenarios': [{'stars': target,
                       'proxy_all_gain': .2 * (percentile(ratings, target) - percentile(ratings, own['stars_rank'])),
                       'peers_above_current_at_or_below_target': sum(own['stars_rank'] < v <= target for v in ratings)}
                      for target in (4.33, 4.35, 4.4)],
        'limit': 'Static public proxy cohort, rounded stars, not official ALL or an achievable-rating forecast.'}
    report['peer_changes'] = diag['peers']['shops']
    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')

        def events(kind, start=lo, end=hi):
            return [dict(json.loads(raw), _ts=ts, _id=eid) for eid, ts, raw in db.execute(
                'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid',
                (kind, start, end))]

        sales = {r['unit']: r for r in events('shop_settled', lo - 172800)}
        refunds = {}
        for row in events('public_refund') + events('public_broken'):
            unit = row.get('unit')
            amount = row.get('refunded', row.get('amount'))
            if unit in sales and type(amount) in (int, float) and amount >= 0:
                refunds[unit] = max(refunds.get(unit, 0), amount)
        legacy_refund = sum(amount for unit, amount in refunds.items() if sales[unit]['_ts'] < lo)
        cohorts = {}
        for kind in ('food', 'goods'):
            rows = [r for r in sales.values() if r['_ts'] >= lo and cat.get(r['sku'], {}).get('kind') == kind]
            gross = sum(r['price'] - r['cost'] for r in rows)
            rf = sum(refunds.get(r['unit'], 0) for r in rows)
            cohorts[kind] = {'sales': len(rows), 'gross_margin': gross, 'known_refunds_from_this_sale_cohort': rf,
                             'gross_less_known_refund': gross - rf,
                             'sold_at_least_6h10m_ago': sum(hi - r.get('paid_at', r['_ts']) >= 22200 for r in rows)}
        report['sale_cohorts'] = cohorts
        # Mechanical scenario only: same starting inventory, no transactions, no
        # new voting fees, known pre-start warranty refunds still paid. All food
        # held past expiry is lost, including units the real shop instead sold.
        storage_idle = goods_cost * .01 * (hi - lo) / 86400
        food_idle = sum(u['cost'] for u in expired_if_idle)
        idle_book = old_own['book'] - storage_idle - food_idle - legacy_refund
        report['idle_mechanical_scenario'] = {
            'starting_goods_cost': goods_cost, 'goods_storage': storage_idle,
            'food_expired_units': len(expired_if_idle), 'food_expired_cost': food_idle,
            'legacy_refunds': legacy_refund, 'ending_book': idle_book,
            'actual_book_minus_idle_book': own['book'] - idle_book,
            'baseline_inventory_units': len(inventory), 'baseline_board_stock_units': old_own.get('stock_units'),
            'baseline_inventory_cost': sum(u['cost'] for u in inventory),
            'baseline_board_stock_cost': old_own.get('stock_cost'),
            'scope': 'Mechanical Book scenario, not causal estimate or ALL: no new supply, no sales, no votes; static known rules; baseline public/private timestamp mismatch retained.'}
        for name, start in [('v28_to_cutoff', lo), ('v281_to_cutoff', v281_at)]:
            roles = defaultdict(lambda: {'calls': 0, 'usd_estimate': 0., 'seconds': 0.})
            for row in events('model_usage', start):
                key = row.get('role', 'unknown') + '/' + row.get('model', 'unknown')
                roles[key]['calls'] += 1
                roles[key]['usd_estimate'] += row.get('estimated_usd', 0)
                roles[key]['seconds'] += row.get('seconds', 0)
            pubs = events('market_research_published', start)
            report[name] = {'hours': (hi - start) / 3600, 'llm_roles': dict(roles),
                            'estimated_usd': sum(r['usd_estimate'] for r in roles.values()),
                            'calls': sum(r['calls'] for r in roles.values()),
                            'research_publications': len(pubs),
                            'approved_trials': sum(bool(r.get('trial_review_approved')) for r in pubs),
                            'critic_cache_skips': len(events('research_critic_skipped', start)),
                            'framework_actions': dict(Counter(r.get('action') for r in events('framework_experiment', start)))}
        lengths = [r[0] for r in db.execute('SELECT length(data) FROM events WHERE kind=? ORDER BY ts DESC LIMIT 500', ('prediction',))]
        report['logging'] = {'sqlite_bytes': (ROOT / '.market/r3.sqlite3').stat().st_size,
                             'latest_prediction_samples': len(lengths),
                             'prediction_mean_json_characters': sum(lengths) / len(lengths) if lengths else None,
                             'prediction_max_json_characters': max(lengths) if lengths else None,
                             'limit': 'Storage/log payload size; not a measured causal latency improvement.'}
    morning = next(r for r in read(MORNING)['responses']['/api/board']['data']['shops'] if r['name'] == 'seochanit')
    report['since_morning'] = {k: own[k] - morning[k] for k in ('book', 'revenue', 'all', 'stars_rank')}
    current_inv = read('.market/inventory.json')
    current_goods = sum(u['cost'] for u in current_inv['units'] if u['kind'] == 'goods')
    end = datetime.fromisoformat('2026-11-17T23:59:00+09:00').timestamp()
    report['terminal_hold_scenario'] = {'remaining_hours_at_cutoff': (end - hi) / 3600,
                                      'private_snapshot_at': datetime.now(timezone.utc).isoformat(),
                                      'current_goods_cost': current_goods,
                                      'goods_storage_if_all_held_to_end': current_goods * .01 * max(0, end - hi) / 86400,
                                      'scope': 'No sale/new supply; does not assume every existing unit should be held.'}
    report['limits'] = ['No randomized LLM on/off experiment; no proven causal LLM trading uplift.',
                        'LLM dollars use configured prices, exclude other applications, and are not the provider invoice.',
                        'Known refund cohorts can still mature; unsold/new inventory expiry and storage are not allocated to sale cohorts.',
                        'Published rounded ratings and public eligible cohort cannot exactly reproduce official ALL.',
                        'Inventory and DB are read after the frozen official board cutoff.']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'logs' / ('shop-last-effort-review-' + stamp + '.json')
    output.open('x', encoding='utf-8').write(json.dumps(report, ensure_ascii=False, indent=2))
    print('SOURCE', output)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
