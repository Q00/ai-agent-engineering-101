"""Reproduce Astra's first-cycle measurements, reading local records only."""
import collections
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profit_cohorts

ROOT = Path(__file__).resolve().parents[1]
TZ = dt.timezone(dt.timedelta(hours=9))
END = dt.datetime(2026, 11, 4, 9, 5, 9, tzinfo=TZ).timestamp()
START = dt.datetime(2026, 11, 3, 21, 5, 9, tzinfo=TZ).timestamp()


def emit(kind, **data):
    print(json.dumps(dict(kind=kind, **data), ensure_ascii=False))


def main():
    rows = profit_cohorts.read_rows(ROOT / '.market/ledger_rows.jsonl')
    rows = [r for r in rows if (r.get('ts') or 0) <= END]
    refs = {r['seq']: f'.market/ledger_rows.jsonl:{i}' for i, r in enumerate(
        profit_cohorts.read_rows(ROOT / '.market/ledger_rows.jsonl'), 1)}
    records = profit_cohorts.read_json(ROOT / 'memory/seller_memory_r3.json', {})['negotiations']
    costs = {n['unit']: n['cost'] for n in records if n.get('result') == 'deal' and n.get('unit') and 'cost' in n}
    logs = []
    day, last = dt.date(2026, 11, 3), 0
    mine = {}
    for i, line in enumerate((ROOT / 'logs/market-kit.log').read_text(errors='replace').splitlines(), 1):
        match = re.match(r'(\d\d):(\d\d):(\d\d)', line)
        if not match:
            continue
        h, m, s = map(int, match.groups()); sec = h * 3600 + m * 60 + s
        if last - sec > 43200:
            day += dt.timedelta(days=1)
        last = sec
        ts = dt.datetime.combine(day, dt.time(h, m, s), TZ).timestamp()
        if ts > END:
            continue
        logs.append((ts, i, line))
        sale = re.search(r'\[acp\] settled .* at (\d+), ledger #(\d+), unit (\S+)', line)
        if sale:
            mine[int(sale[2])] = dict(amount=int(sale[1]), unit=sale[3], ts=ts, ref=f'logs/market-kit.log:{i}')
    losses = profit_cohorts.read_rows(ROOT / 'memory/agent/losses.jsonl')
    refunds = {int(re.search(r'ledger #(\d+)', x['detail'])[1]): x['amount'] for x in losses
               if x['kind'] == 'break_refund' and x['ts'] <= END}
    def window(lo, hi):
        own = [(seq, r) for seq, r in mine.items() if lo <= r['ts'] < hi]
        ls = [(i, l) for ts, i, l in logs if lo <= ts < hi]
        missing = sum(r['unit'] not in costs for _, r in own)
        known_net = sum(r['amount'] - costs[r['unit']] - refunds.get(seq, 0) for seq, r in own if r['unit'] in costs)
        market = sum(r['k'] == 'settle' and lo <= r['ts'] < hi for r in rows)
        return dict(paid=len(own), revenue=sum(r['amount'] for _, r in own), known_net=known_net,
                    missing_cost=missing, linked_refunds=sum(refunds.get(seq, 0) for seq, _ in own),
                    market_paid=market, supply=sum('[supply]' in l and 'refused' not in l for _, l in ls),
                    supply_refused=sum('[supply]' in l and 'refused' in l for _, l in ls),
                    no_stock=sum('REJECTED (not in stock)' in l for _, l in ls),
                    cut_lines=sum('[restock] guard:' in l and ' -> ' in l for _, l in ls),
                    fallback=sum('fallback: unambiguous numeric' in l for _, l in ls),
                    buyer_paid=sum('[acp] paid' in l for _, l in ls),
                    buyer_not_paying=sum('[acp] not paying' in l for _, l in ls),
                    samples=[r['ref'] for _, r in own[:1]+own[-1:]])
    emit('method', cutoff='2026-11-04T09:05:09+09:00', notes='각 커밋 전후 대칭 최대 30분. paid는 kit settled, 순기여는 유료 기억 원가와 판매 seq 환불 연결. missing_cost>0이면 총 순기여 확정 불가. 동시 변경·시장 수요·보증 미숙성으로 인과 추정 아님.', all=window(START, END))
    git = subprocess.check_output(['git', 'log', '--reverse', '--since=2026-11-03T21:05:09+09:00', '--format=%h%x09%ct%x09%s', '--', '.'], cwd=ROOT, text=True)
    for line in git.splitlines():
        sha, ts, title = line.split('\t', 2); ts = int(ts)
        if ts >= END:
            continue
        width = min(1800, END-ts)
        emit('commit', commit=sha, title=title, minutes=round(width/60, 2),
             before=window(ts-width, ts), after=window(ts, ts+width))
    cohort = profit_cohorts.build(str(ROOT), END)
    for sku in ['G1515d951','G6b9db161','G0f415407','G9c02e68e','G75f11c19','G200e39a3']:
        emit('cohort', sku=sku, **cohort['skus'].get(sku, {}))
    shop_names = {r['shop']:r['seller'] for r in rows if r.get('seller') and r.get('shop')}
    catalog = profit_cohorts.read_json(ROOT / '.market/catalog.json', {})
    for name in ['dae-yangkim', 'Inextro Market', 'dragonash', 'Grand Furniture', 'Nocha Furniture']:
        ss = [r for r in rows if r['k']=='settle' and shop_names.get(r.get('shop'))==name]
        top = collections.Counter(r['sku'] for r in ss).most_common(3)
        emit('rival', name=name, paid=len(ss), revenue=sum(r['amount'] for r in ss),
             top=[dict(sku=sku, paid=n, revenue=sum(r['amount'] for r in ss if r['sku']==sku),
                       list_price=catalog.get(sku, {}).get('list_price'),
                       buyers=collections.Counter(r['buyer'] for r in ss if r['sku']==sku).most_common(3),
                       ref=refs[next(r['seq'] for r in ss if r['sku']==sku)]) for sku,n in top],
             limitation='경쟁점 원가는 공개되지 않으므로 마진은 미확인; URL/영수증 이름 연결이며 카드 이름과 다를 수 있음')
    for i, d in enumerate(profit_cohorts.read_rows(ROOT / 'meta/grades.jsonl'),1):
        emit('terra', cycle=d['cycle'], status=d['status'], action=d.get('action'),
             strategy_check=d.get('strategy_check'), ref=f'meta/grades.jsonl:{i}')
    calls = profit_cohorts.read_rows(ROOT / '.market/tool_calls.jsonl')
    for path in sorted((ROOT / 'tools').glob('*/manifest.json')):
        name=path.parent.name; used=[c for c in calls if c.get('tool')==name and c.get('ts',0)<=END]
        emit('tool', name=name, calls=len(used), failures=sum(not c.get('ok') for c in used), selftest='selftest' in json.loads(path.read_text()))
    used = [c for c in calls if c.get('tool')=='auto_market_lookup']
    emit('tool', name='auto_market_lookup', calls=len(used), failures=sum(not c.get('ok') for c in used), present=(ROOT/'tools/auto_market_lookup').exists())
    forum=profit_cohorts.read_json(ROOT/'.market/forum.json', [])
    emit('forum', demand=[{k:x.get(k) for k in ('id','author','text','score')} for x in forum if '구합니다' in x['text']],
         top=[{k:x.get(k) for k in ('id','author','text','score')} for x in sorted(forum,key=lambda x:-(x.get('score') or 0))[:3]])


if __name__ == '__main__':
    main()
