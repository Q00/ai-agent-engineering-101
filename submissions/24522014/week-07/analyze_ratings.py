"""Compare counted public reviews with the earlier conversion-analysis snapshot."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl

import httpx

ROOT = Path(__file__).resolve().parent


def describe(entries):
    rows = [e for e in entries if e['data'].get('verified') and isinstance(e['data'].get('stars'), (int, float))]
    dist = Counter(str(e['data']['stars']) for e in rows)
    buyers = defaultdict(lambda: {'reviews': 0, 'sum': 0, 'lost_from_five': 0, 'stars': Counter()})
    for e in rows:
        d = e['data']; b = buyers[d['buyer']]
        b['reviews'] += 1; b['sum'] += d['stars']; b['lost_from_five'] += 5-d['stars']; b['stars'][str(d['stars'])] += 1
    return {'reviews':len(rows),'sum':sum(e['data']['stars'] for e in rows),
            'mean':sum(e['data']['stars'] for e in rows)/len(rows) if rows else None,
            'distribution':dist,'buyers':dict(buyers)}


def main():
    baseline_path = ROOT/'logs/conversion-analysis/20261021T025416Z/public-evidence.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = ROOT/'logs/rating-analysis'/stamp
    out.mkdir(parents=True)
    with httpx.Client(verify=ssl.create_default_context(),timeout=50) as c:
        def get(path):
            r=c.get('https://market.ourolabs.ai'+path);r.raise_for_status();return r.json()
        board=get('/api/board?round=2');shop=next(s for s in board['shops'] if s['name']=='kcwarni')
        last=get('/api/ledger?after=-1')[0]['seq']
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages=list(pool.map(lambda seq:get(f'/api/ledger?after={seq}'),range(baseline['snapshot_seq'],last,500)))
    entries=sorted({e['seq']:e for page in pages for e in page if baseline['snapshot_seq']<e['seq']<=last}.values(),key=lambda e:e['seq'])
    assert len(entries)==last-baseline['snapshot_seq']
    urls=set(baseline['urls'])|{shop['card']['url']}
    urls.update(e['data']['url'] for e in entries if e['kind']=='join' and e['data'].get('name')=='kcwarni' and e['data'].get('role')=='shop')
    new=[e for e in entries if e['kind']=='result' and e['round']==2 and e['data'].get('shop') in urls]
    all_rows=baseline['results']+new
    evidence={'baseline':str(baseline_path.relative_to(ROOT)),'baseline_sha256':hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
              'last_seq':last,'checked_at':datetime.now(timezone.utc).isoformat(),'board':shop,'new_results':new}
    (out/'public-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={'baseline':describe(baseline['results']),'added':describe(new),'total':describe(all_rows),
             'recent_low_reviews':[e for e in all_rows if e['data'].get('verified') and (e['data'].get('stars') or 5)<5][-45:]}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print('EVIDENCE',out)
    print(json.dumps({key:summary[key] for key in ('baseline','added','total')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
