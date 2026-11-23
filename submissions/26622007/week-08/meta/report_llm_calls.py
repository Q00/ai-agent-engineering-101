"""REPORT 1.4절의 LLM 호출 집계: .market/llm_calls.jsonl을 board 정산 수가 남은 시각 사이 구간으로 나눈다.

호출 한 줄은 harness.record_call()이 남긴다(거래 에이전트의 Luna·Jev 호출). 메타 하네스(Terra·Astra)의 호출은 이 파일에 없다.
정산 수는 meta/evidence의 board 스냅숏(REPORT 4.3절 표)에서 가져온다.

    python3 -I meta/report_llm_calls.py <.market/llm_calls.jsonl> > meta/evidence/report-1124-llm-calls.json
"""
import collections
import datetime as dt
import json
import sys

KST = dt.timezone(dt.timedelta(hours=9))
# (시각, 가게 정산, buyer 정산, 근거 파일)
POINTS = [
    ('11/05 12:25', 3596, 1798, 'terra-1105-122527.json'),
    ('11/06 03:14', 5050, 2450, 'terra-1106-031450.json'),
    ('11/07 13:48', 9759, 3967, 'terra-1107-134856.json'),
    ('11/08 17:04', 16545, 5591, 'terra-1108-170417.json'),
    ('11/09 14:17', 21488, 7213, 'terra-1109-141711.json'),
    ('11/10 20:16', 28213, 9722, 'terra-1110-201617.json'),
    ('11/11 18:33', 33272, 11441, 'terra-1111-183339.json'),
    ('11/17 09:20', 55011, 19898, 'report-1117-board.json'),
    ('11/17 15:19', 55928, 20178, 'report-1117-1519-board.json'),
]
ROUND3 = ('11/03 00:00', '11/18 00:00')


def ts(s):
    return dt.datetime.strptime('2026/' + s, '%Y/%m/%d %H:%M').replace(tzinfo=KST).timestamp()


def window(rows, a, b):
    sel = [r for r in rows if ts(a[0]) <= r['ts'] < ts(b[0])]
    hours = (ts(b[0]) - ts(a[0])) / 3600
    shop, buyer = b[1] - a[1], b[2] - a[2]
    seller_calls = sum(r['tag'].startswith('seller') for r in sel)
    buyer_calls = sum(r['tag'].startswith('buyer') for r in sel)
    return {'from': a[0], 'to': b[0], 'hours': round(hours, 1),
            'shop_deals': shop, 'buyer_deals': buyer, 'calls': len(sel),
            'seller_calls': seller_calls, 'buyer_calls': buyer_calls,
            'calls_per_deal': round(len(sel) / (shop + buyer), 2),
            'seller_calls_per_shop_deal': round(seller_calls / shop, 2),
            'buyer_calls_per_buyer_deal': round(buyer_calls / buyer, 2),
            'deals_per_hour': round((shop + buyer) / hours),
            'calls_per_hour': round(len(sel) / hours),
            'calls_by_tag': dict(collections.Counter(r['tag'] for r in sel).most_common()),
            'cost_usd': round(sum(r.get('cost') or 0 for r in sel), 2),
            'rows_with_cost': sum(r.get('cost') is not None for r in sel)}


def main(path):
    rows = [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]
    r3 = [r for r in rows if ts(ROUND3[0]) <= r['ts'] < ts(ROUND3[1])]
    by_tag = collections.Counter(r['tag'] for r in r3)
    by_day = collections.Counter(dt.datetime.fromtimestamp(r['ts'], KST).strftime('%m/%d') for r in r3)
    medians = {}
    for tag in by_tag:
        s = sorted(r['seconds'] for r in r3 if r['tag'] == tag and r.get('seconds') is not None)
        medians[tag] = s[len(s) // 2] if s else None
    out = {
        'source': 'market 계정 .market/llm_calls.jsonl (gitignore, 맥 미니)',
        'first_row': dt.datetime.fromtimestamp(rows[0]['ts'], KST).isoformat(),
        'round3_rows': len(r3), 'round3_ok': sum(bool(r.get('ok')) for r in r3),
        'round3_cost_usd': round(sum(r.get('cost') or 0 for r in r3), 2),
        'round3_rows_with_cost': sum(r.get('cost') is not None for r in r3),
        'round3_by_backend': dict(collections.Counter(str(r.get('backend')) for r in r3)),
        'round3_by_tag': dict(by_tag.most_common()),
        'round3_median_seconds_by_tag': medians,
        'round3_by_day': dict(sorted(by_day.items())),
        'windows': [window(rows, a, b) for a, b in zip(POINTS, POINTS[1:])],
        'before_after': [window(rows, POINTS[0], POINTS[2]), window(rows, POINTS[6], POINTS[8])],
        'deal_points': [{'at': p[0], 'shop_deals': p[1], 'buyer_deals': p[2], 'evidence': p[3]} for p in POINTS],
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == '__main__':
    main(sys.argv[1])
