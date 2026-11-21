"""Read-only review from frozen boards and a bounded journal transaction.

Run after compare_market_peers.py and review_evening_progress_20261110.py.
Never changes trading policy, credentials, account identity or runtime state.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
import json
import sqlite3
import statistics
import sys

from r3.state import ROOT, read_json
from r3.shop_growth import inventory_limit

KST = timezone(timedelta(hours=9))


def main():
    review_path, proof_path = sys.argv[1:3]
    review = read_json(ROOT / review_path)
    source = read_json(ROOT / review['source'])
    proof = read_json(ROOT / proof_path)
    start, end = review['runtime']['started_at'], review['cutoff']
    duration = end - start
    comparisons = review['comparisons']
    cfg = read_json(ROOT / 'policy.json')

    def dist(values):
        if not values:
            return {'n': 0}
        values = sorted(values)
        return {'n': len(values), 'median': statistics.median(values),
                'p95_nearest_rank': values[max(0, __import__('math').ceil(.95 * len(values)) - 1)]}

    with sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')

        def events(kind, lo, hi=end):
            return [dict(json.loads(raw), _ts=ts) for ts, raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',
                (kind, lo, hi))]

        operational = {}
        for label, lo, hi in [('before_equal_duration', start-duration, start), ('since_deploy', start, end)]:
            sales = {r['unit']: r for r in events('shop_settled', lo, hi)}
            latency = {}
            for kind in ['shop_fast_execution', 'buyer_quote_latency', 'buyer_worker_execution']:
                rows = events(kind, lo, hi)
                latency[kind] = dist([r['seconds'] for r in rows if type(r.get('seconds')) in (int, float)])
            operational[label] = {
                'start_kst': datetime.fromtimestamp(lo, KST).isoformat(),
                'end_kst': datetime.fromtimestamp(hi, KST).isoformat(),
                'hours': (hi-lo)/3600, 'shop_sales': len(sales),
                'shop_revenue': sum(r['price'] for r in sales.values()),
                'shop_gross_margin': sum(r['price']-r['cost'] for r in sales.values()),
                'latency_seconds': latency,
                'restock_blocked_reasons': dict(Counter(r['reason'] for r in events('restock_blocked', lo, hi))),
                'supply_execution_status_counts': dict(Counter(r['status'] for r in events('supply_execution_status', lo, hi))),
            }
        stockouts = events('stockout', start)
        operational['stockout_events_since_deploy'] = len(stockouts)
        operational['error_counts_since_deploy'] = {
            kind: len(events(kind, start)) for kind in [
                'market_control_error', 'cycle_supervisor_error', 'cycle_supply_error',
                'buyer_worker_error', 'strategy_error', 'forum_error']}

    board = source['responses']['/api/board']['data']
    peers = [{k: row.get(k) for k in ['name', 'all', 'book', 'revenue', 'deals', 'stock_units', 'stock_cost', 'stars_rank']}
             for row in board['shops'] if row['name'] in ['seochanit', 'yms979', 'moss-and-oak', 'inextro', '25620027']]
    latest_comp = comparisons[-1]
    own_change = {role: next(r['changes'] for r in latest_comp['peers'][role] if r['name']=='seochanit')
                  for role in ['shops', 'buyers', 'players']}
    # Independently reconcile local sales and the official short-window endpoints.
    assert own_change['shops']['deals']['delta'] == operational['since_deploy']['shop_sales']
    assert own_change['shops']['revenue']['delta'] == operational['since_deploy']['shop_revenue']
    capacity = review['control']['cash_capacity']
    result = {
        'sources': [review_path, proof_path, review['source'], 'review_evening_progress_20261110.py',
                    'review_live_progress_20261111.py', *[c['source'] for c in comparisons]],
        'board_cutoff_kst': review['cutoff_kst'], 'connection_checked_at_utc': proof['at_utc'],
        'current': review['current'], 'comparisons': comparisons, 'buyer_windows': review['buyer_windows'],
        'operations': operational, 'control_sample_diagnostics': review['operations'],
        'inventory_snapshot': review['inventory'], 'capacity_snapshot': {k:v for k,v in capacity.items() if k!='reserve_release_schedule'},
        'effective_inventory_ceiling': inventory_limit(cfg), 'selected_shop_peers': peers,
        'framework': review['framework'], 'new_goods_maturity': review['quality_since_framework']['goods'],
        'framework_live_counts': proof['counts_since_restart'],
        'connection_proof': {k: proof[k] for k in ['online', 'listed', 'public_statuses', 'independent_recovery_guard_active', 'source_mismatches']},
        'daily_usage': proof['daily_usage'],
        'reconciliation': {'official_shop_deals_equal_journal': True, 'official_shop_revenue_equal_journal': True},
        'limitations': review['limits'] + [
            'The strategy deployment snapshot begins 77 seconds before runtime startup; Buyer board delta 182 and post-start journal purchases 178 have different windows.',
            'Registry and board requests can race an address rotation; a temporary zero deal count on one earlier board was not a persistent loss: 8542 deals are present at the frozen cutoff.',
            'Control sample counts are not outage durations or causal lost sales.',
            'Inventory, control and usage are read at their recorded snapshot times, not reconstructed to the frozen board cutoff.',
            'The effective inventory ceiling is 180, not the legacy base target of 60; 66 current units do not bind that ceiling.',
            'A supply execution status is an execution record, not independently reconciled acquired inventory.',
        ],
    }
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    prefix = f'progress-readout-{stamp}'
    path = ROOT / 'logs' / (prefix + '.json')
    path.open('x', encoding='utf-8').write(json.dumps(result, ensure_ascii=False, indent=2))

    rows = [{'Shop': r['name'], 'Book 증가 (만원)': r['changes']['book']['delta']/10000}
            for r in latest_comp['peers']['shops']]
    chart = {'id': 'shop-book-growth-20261111', 'title': '같은 기간 Shop Book 증가: 11/11 14:16–16:51',
             'description': '공식 보드 두 시점의 차이. 순수 전략 효과나 향후 수익 예측이 아닙니다.',
             'height': 340, 'spec': {'type': 'horizontalBar', 'x': 'Book 증가 (만원)', 'y': 'Shop',
                 'xLabel': 'Book 증가 (만원)', 'showValues': True,
                 'valueFormat': {'style': 'decimal', 'maximumFractionDigits': 1}}, 'rows': rows}
    receipt = {'schemaVersion': 1, 'items': [{
        'id': 'shop-buyer-progress', 'title': 'Shop·Buyer 진행 경과와 동일 기간 비교', 'queries': [{
            'id': 'frozen-board-and-journal',
            'source': {'label': '공식 시장 보드·등록 목록 및 자체 거래 저널',
                'files': result['sources'], 'links': [{'label': '공식 보드', 'url': 'https://market.ourolabs.ai/api/board'}],
                'metricDefinitions': [
                    {'label': 'Book 증가', 'definition': '동일 Shop의 공식 누적 Book 두 시점 차이. 원 단위를 10,000으로 나누어 만원으로 표시합니다.'},
                    {'label': '전체 순위', 'definition': '공개된 점수가 있는 참가자에서 자신보다 점수가 높은 수 + 1. 학생 순위는 별도 모집단입니다.'},
                    {'label': 'Buyer 구매 성공률', 'definition': '기간 안에 생성되고 종료된 주문 중 실제 구매한 주문 수 / 종료 주문 수. 식품과 공산품을 분리합니다.'}],
                'filters': ['보드 비교: 두 시점에 같은 이름으로 존재하는 상위 Shop 5곳과 seochanit.',
                            '저널 비교: 실행 시작 전후 동일한 2.565시간. 미종료 주문은 성공률 분모에서 제외.',
                            'Shop 매출 대조: unit별 중복 제거한 shop_settled 기록.'],
                'sql': 'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',
                'caveats': result['limitations']},
            'reportingPeriod': latest_comp['start_kst'] + ' to ' + review['cutoff_kst'],
            'methods': [{'language': 'calculation', 'code': 'Book change (만원) = (current official book - prior official book) / 10000; ranks = 1 + count(scored participants with strictly higher official score).'}],
            'columns': ['Shop', 'Book 증가 (만원)'], 'rows': rows,
            'preview': {'kind': 'aggregate', 'note': '같은 기간 비교 대상 여섯 Shop의 집계입니다.'}}]}]}
    for suffix, value in [('-chart.json', chart), ('-sources.json', receipt)]:
        (ROOT / 'logs' / (prefix+suffix)).open('x', encoding='utf-8').write(json.dumps(value, ensure_ascii=False, indent=2))
    print(json.dumps({'analysis': str(path.relative_to(ROOT)), 'prefix': prefix,
                      'operations': operational, 'connection_proof': result['connection_proof']}, ensure_ascii=True))


if __name__ == '__main__':
    main()
