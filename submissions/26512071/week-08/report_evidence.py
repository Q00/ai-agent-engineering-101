"""Read-only report audit; writes only a new evidence directory, never edits market logs.
Usage: python submissions/26512071/week-07/report_evidence.py
No model calls, market orders, process restarts, or private-key reads.
"""
from pathlib import Path
from datetime import datetime
from collections import Counter
import hashlib
import json
import re
import subprocess
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NOW = datetime.now().astimezone()
OUT = HERE / 'logs' / ('report-audit-' + NOW.strftime('%Y%m%d-%H%M%S'))
OUT.mkdir(parents=True, exist_ok=False)

def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def rows(name):
    data = (HERE / name).read_bytes()
    parsed = [json.loads(s) for s in data.decode('utf-8').splitlines() if s.strip()]
    kept = [r for r in parsed if not r.get('test')]
    tasks = [r.get('task') for r in kept if r.get('task')]
    assert len(tasks) == len(set(tasks)), name + ': duplicate task ids'
    return kept, {'sha256': digest(data), 'bytes': len(data), 'rows': len(parsed),
                  'excluded_test_rows': len(parsed) - len(kept), 'kept': len(kept),
                  'first_time': kept[0].get('time'), 'last_time': kept[-1].get('time')}

sales, sales_meta = rows('sales.jsonl')
talks, talks_meta = rows('talks.jsonl')
memory, memory_meta = rows('memory.jsonl')

def sale_stats(rr):
    return {'deals': len(rr), 'at_reserve': sum(r['price'] == r['reserve'] for r in rr),
            'local_margin_sum': sum(r['price'] - r['reserve'] for r in rr),
            'items': {item: {'deals': len(rs), 'average_price': sum(r['price'] for r in rs) / len(rs),
                             'average_price_over_reserve': sum(r['price']/r['reserve'] for r in rs)/len(rs)}
                      for item in ('lamp', 'chair', 'bookshelf')
                      if (rs := [r for r in rr if r['item'] == item])}}

log_bytes = (HERE / 'logs/market-kit.log').read_bytes()
log = log_bytes.decode('utf-8', errors='replace')
lines = log.splitlines()
# Before the first pricing change, using only the initial 10-20 log segment.
before_tasks = 0
before_sales = []
reserves = {'lamp': 100, 'chair': 180, 'bookshelf': 260}
for line in lines:
    m = re.match(r'(\d\d:\d\d:\d\d) (.*)', line)
    if not m:
        continue
    if m[1] >= '20:50:00':
        break
    before_tasks += bool(re.search(r'\[seller\] task=\w+ picked', m[2]))
    deal = re.search(r'\[seller\] task=\w+ (lamp|chair|bookshelf) offer=\S+ -> accept (\d+)', m[2])
    if deal:
        before_sales.append({'item': deal[1], 'price': int(deal[2]), 'reserve': reserves[deal[1]]})

markers = ['[hold]', '[pricing]', 'ally (BUYER_ALLIES)', 'ADVERTISED inside',
           'skipping willow-goods: my memory has a bad receipt', 'sig_seller ok, sig_buyer added',
           'receipt has no sig_seller']
rep = json.loads((HERE / 'test-report.json').read_text(encoding='utf-8'))
source_match = {}
for name in ('seller.py', 'buyer.py', 'market_buyer.py', 'agent.json'):
    rel = (HERE / name).relative_to(ROOT).as_posix()
    before = subprocess.run(['git', '-C', str(ROOT), 'show', '688e5c8:' + rel], capture_output=True, check=True).stdout
    source_match[name] = before.replace(b'\r\n', b'\n') == (HERE/name).read_bytes().replace(b'\r\n', b'\n')

# Read only an explicit whitelist of non-secret deployment settings; never copy claim codes.
deploy = Path.home() / 'agent-market'
safe_names = ['SHOP_PROMO','SHOP_NICK','BUYER_EXPLORE','SHOP_BUSY','SHOP_HOLD','SHOP_PRICING',
              'SHOP_ALLIES','BUYER_ALLIES','BUYER_ALLY_RATE']
safe_config = {}
for filename in ('rejoin.ps1', 'run-forever.ps1', 'watch-then-loop.ps1'):
    f = deploy / filename
    if f.exists():
        safe_config[filename] = [line.strip() for line in f.read_text(encoding='utf-8-sig').splitlines()
                                if any(re.match(r'^\s*\$env:' + name + r'\s*=', line, re.I) for name in safe_names)]
pricing_file = deploy / 'pricing.json'
pricing = json.loads(pricing_file.read_text(encoding='utf-8-sig')) if pricing_file.exists() else None

boards = []
for round_no in (0, 1, 2):
    url = 'https://market.ourolabs.ai/api/board?round=' + str(round_no)
    record = {'url': url, 'requested_at': datetime.now().astimezone().isoformat()}
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            raw = response.read()
            record['status'] = response.status
        data = json.loads(raw)
        save('board-round-' + str(round_no) + '.json', data)
        record.update({'sha256': digest(raw), 'round': data.get('round'),
                       'shops': [r for r in data.get('shops', []) if r.get('name') in ('kimchyoungman','fair-furniture')],
                       'buyers': [r for r in data.get('buyers', []) if r.get('name') in ('kimchyoungman','fair-furniture')]})
    except Exception as exc:
        record.update({'error_type': type(exc).__name__, 'error': str(exc)})
    boards.append(record)

outcomes = Counter(r.get('outcome') for r in talks)
summary = {'captured_at': NOW.isoformat(), 'scope': 'local log snapshots; public board GETs; saved test receipt, not a fresh market test',
           'files': {'sales.jsonl': sales_meta, 'talks.jsonl': talks_meta, 'memory.jsonl': memory_meta,
                     'logs/market-kit.log': {'sha256': digest(log_bytes), 'bytes': len(log_bytes), 'lines': len(lines)}},
           'baseline_before_20261020_2050': {'started_negotiations': before_tasks, 'sales': sale_stats(before_sales),
                                               'accepted_over_started': len(before_sales)/before_tasks},
           'sales': {label: sale_stats(rr) for label, rr in [('all',sales),('promo',[r for r in sales if r.get('promo')]),
                                                          ('normal',[r for r in sales if not r.get('promo')])]},
           'talk_outcomes': dict(outcomes), 'talk_close_rate': outcomes['deal']/len(talks),
           'memory': {'valid_local_receipt_rows': sum(r.get('price') is not None for r in memory),
                      'receipt_problems': dict(Counter(r.get('receipt_problem') or 'none' for r in memory)),
                      'ally_shop_trades': dict(Counter(r.get('shop') for r in memory if r.get('shop') == 'ggari-shop'))},
           'log_counts': {m: log.count(m) for m in markers},
           'pricing_events': [line for line in lines if '[pricing]' in line],
           'recent_log_tail': lines[-12:], 'safe_deployment_config': safe_config,
           'pricing_file_snapshot': pricing, 'boards': boards,
           'saved_test': {'started':rep['started'], 'runs':len(rep['results']),
                          'passing':sum(bool(r['pass']) for r in rep['results']), 'passed':rep['passed'],
                          'kit_hash_matches': digest((ROOT/'scripts/market_test_week07.py').read_bytes().replace(b'\r\n',b'\n')) == rep['kit_sha256'],
                          'source_matches_688e5c8': source_match}}
save('summary.json', summary)
print('EVIDENCE_DIRECTORY', OUT)
print(json.dumps(summary, ensure_ascii=False, indent=2))
