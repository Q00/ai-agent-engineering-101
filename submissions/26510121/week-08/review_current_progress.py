"""Compare frozen official boards and journal events at the same public cutoff."""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import sqlite3
import sys
from r3.state import ROOT, read_json


def main():
    before_path, after_path = sys.argv[1:3]
    sources = [read_json(ROOT / p) for p in [before_path, after_path]]
    boards = [s['responses']['/api/board']['data'] if 'responses' in s else s['board'] for s in sources]
    start, end = [s['responses']['/api/board']['finished_at'] if 'responses' in s else s['at'] for s in sources]
    hours = (end-start)/3600
    result = {'sources': [before_path, after_path], 'hours': hours,
              'start_kst': datetime.fromtimestamp(start, timezone(timedelta(hours=9))).isoformat(),
              'end_kst': datetime.fromtimestamp(end, timezone(timedelta(hours=9))).isoformat(),
              'roles': {}, 'class': {}}
    for role, key, metric in [('shops', 'name', 'all'), ('buyers', 'name', 'all'), ('players', 'player', 'total')]:
        rows = boards[1][role]
        old = {r[key]: r for r in boards[0][role]}
        mine = next(r for r in rows if r[key] == 'seochanit')
        prior = old['seochanit']
        scored = [r for r in rows if type(r.get(metric)) in (int, float)]
        old_scored = [r for r in old.values() if type(r.get(metric)) in (int, float)]
        peers = []
        for r in rows:
            if r[key] not in old:
                continue
            fields = ['book', 'revenue', 'deals', 'all', 'saved', 'forum', 'posts', 'orders', 'served', 'refunds', 'total']
            changes = {f: r[f]-old[r[key]][f] for f in fields
                       if type(r.get(f)) in (int, float) and type(old[r[key]].get(f)) in (int, float)}
            peers.append({'name': r[key], 'changes': changes,
                          'book_per_hour': changes.get('book', 0)/hours})
        result['roles'][role] = {
            'before': prior, 'after': mine, 'rank': 1+sum(r[metric] > mine[metric] for r in scored),
            'population': len(scored),
            'before_rank': 1+sum(r[metric] > prior[metric] for r in old_scored), 'peers': peers,
            'leaders': sorted(scored, key=lambda r: r[metric], reverse=True)[:3]}
    for field in ['student_shop', 'student_buyer', 'student_total']:
        pairs = []
        for b in boards:
            rows = [r for r in b['players'] if r.get('student') and type(r.get(field)) in (int, float)]
            mine = next(r for r in rows if r['player'] == 'seochanit')
            pairs.append({'score': mine[field], 'rank': 1+sum(r[field] > mine[field] for r in rows), 'population': len(rows)})
        result['class'][field] = {'before': pairs[0], 'after': pairs[1]}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro', uri=True) as c:
        c.execute('BEGIN')
        def events(kind):
            return [dict(json.loads(d), _ts=t) for t, d in c.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts', (kind, start, end))]
        sales = events('shop_settled')
        owned = {json.loads(d).get('unit') for d, in c.execute('SELECT data FROM events WHERE kind=?', ('shop_settled',))}
        refunds = [r for r in events('public_broken') if r.get('unit') in owned and (r.get('refunded') or 0) > 0]
        gross_margin = sum(r['price']-r['cost'] for r in sales)
        refunded = sum(r['refunded'] for r in refunds)
        before = result['roles']['shops']['before']; after = result['roles']['shops']['after']
        result['shop_window'] = {
            'sales': len(sales), 'gross_revenue': sum(r['price'] for r in sales),
            'gross_margin': gross_margin, 'below_cost_sales': sum(r['price'] < r['cost'] for r in sales),
            'refunds': refunds, 'refund_amount': refunded,
            'book_delta': after['book']-before['book'],
            'other_book_reduction_residual': gross_margin-refunded-(after['book']-before['book']),
            'board_revenue_reconciliation_residual': sum(r['price'] for r in sales)-refunded-(after['revenue']-before['revenue']),
            'top_margin_skus': sorted([{'sku': sku, 'margin': sum(r['price']-r['cost'] for r in sales if r['sku'] == sku)}
                                      for sku in {r['sku'] for r in sales}], key=lambda r: r['margin'], reverse=True)[:5],
            'last_restock_decision': events('restock_decision')[-1:]}
        purchases = events('purchase'); outcomes = events('outcome')
        matured = {r['order'] for r in outcomes if r.get('matured')}
        results = events('market_result')
        orders={r['order']:r for r in events('buyer_order')}
        quotes=events('quote_comparison')
        attempt_counts=Counter((orders.get(r.get('order'),{}).get('kind'),r.get('kind'),r.get('reason'))
                               for r in events('attempt'))
        public_purchases=[r for r in events('public_settle') if r.get('buyer')=='seochanit']
        result['buyer_window'] = {
            'settled_purchases': len(purchases), 'purchase_mix': dict(Counter(r['kind'] for r in purchases)),
            'initial_saved_contribution_mean': sum(r['initial_saved_contribution'] for r in purchases)/len(purchases) if purchases else None,
            'purchases_with_matured_outcome_by_cutoff': sum(r['order'] in matured for r in purchases),
            'results': len(results), 'verified_results': sum(bool(r.get('verified')) for r in results),
            'prepayment_refusals': dict(Counter(r['reason'] for r in events('buyer_offer_refusal'))),
            'retry_attempts': len(events('checkout_attempt')),
            'public_settled_purchases':len(public_purchases),
            'order_mix':dict(Counter(r.get('kind') for r in orders.values())),
            'funnel_by_kind':{kind:{'orders':sum(r.get('kind')==kind for r in orders.values()),
                'paid_results':sum(orders.get(r['order'],{}).get('kind')==kind and bool(r.get('verified')) for r in results),
                'live_quotes':sum(orders.get(r['order'],{}).get('kind')==kind for r in quotes),
                'feasible_live_quotes':sum(orders.get(r['order'],{}).get('kind')==kind and r.get('feasible_at_ask') for r in quotes)}
                for kind in ['food','goods']},
            'attempt_diagnostics':[{'order_kind':kind,'type':typ,'reason':reason,'count':count}
                                    for (kind,typ,reason),count in attempt_counts.items()],
            'goods_quote_examples':[{k:r.get(k) for k in ['order','sku','ask','assessment','feasible_at_ask']}
                for r in quotes if orders.get(r['order'],{}).get('kind')=='goods'][:5],
            'forum_deliveries': {role: sum(r.get('role') == role and r.get('status') == 200 for r in events('forum_delivery'))
                                 for role in ['buyer', 'shop']}}
    result['limits'] = [
        'Public board requests and local journal events can straddle individual in-flight orders.',
        'Public revenue is reconciled after refunds; gross margin is before refunds and other costs.',
        'Other book reduction is a reconciliation residual, not independently attributed entirely to storage.',
        'Buyer initial savings are not final Saved: use failures and refunds can revise them.',
        'Global and student percentiles use different populations and cannot be interchanged.',
        'Short-window peer growth is an observation, not a forecast or causal strategy evaluation.']
    if any('responses' not in s for s in sources):
        result['limits'].append('Deployment snapshot time approximates its cached board cutoff, typically within a polling interval.')
    path = ROOT/'logs'/('current-progress-review-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ANALYSIS', path)
    print(json.dumps({k: result[k] for k in ['start_kst', 'end_kst', 'class', 'shop_window', 'buyer_window']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
