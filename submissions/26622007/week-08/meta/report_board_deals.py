"""REPORT 1.4절의 정산 수 비교: 공개 board(/api/board)에서 가게·buyer 정산 수 순위만 뽑는다.

다른 참가자는 이름 없이 순위·정산 수만 남긴다.

    curl -s https://market.ourolabs.ai/api/board -o board.json
    python3 -I meta/report_board_deals.py board.json "<받은 시각>" > meta/evidence/report-1124-board-deals.json
"""
import json
import sys

ME = 'Nocha12'


def ranked(items, me_name):
    items = sorted(items, key=lambda x: -(x.get('deals') or 0))
    mine = next(x for x in items if x['name'] == me_name)
    return {'total': len(items), 'non_house': sum(not x.get('house') for x in items),
            'my_deals': mine['deals'], 'my_rank_by_deals': 1 + sum((x.get('deals') or 0) > mine['deals'] for x in items),
            'top10_deals': [x.get('deals') for x in items[:10]],
            'median_deals': items[len(items) // 2].get('deals')}


def main(path, fetched):
    d = json.load(open(path, encoding='utf-8'))
    me = next(p for p in d['players'] if p['player'] == ME)
    students = sorted((p for p in d['players'] if p.get('student')), key=lambda p: -(p.get('student_total') or 0))
    shops = {s['name']: s for s in d['shops']}
    buyers = {b['name']: b for b in d['buyers']}
    top = []
    for i, p in enumerate(students[:10], 1):
        s, b = shops.get((p.get('shop') or {}).get('name'), {}), buyers.get((p.get('buyer') or {}).get('name'), {})
        top.append({'student_rank': i, 'me': p['player'] == ME, 'student_total': p.get('student_total'),
                    'shop_deals': s.get('deals'), 'buyer_deals': b.get('deals'), 'buyer_orders': b.get('orders')})
    out = {'fetched': fetched + ' https://market.ourolabs.ai/api/board', 'round': d.get('round'), 'totals': d.get('totals'),
           'me': {'total': me.get('total'), 'student_total': me.get('student_total')},
           'shops': ranked(d['shops'], me['shop']['name']), 'buyers': ranked(d['buyers'], me['buyer']['name']),
           'students_top10_anonymous': top}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
