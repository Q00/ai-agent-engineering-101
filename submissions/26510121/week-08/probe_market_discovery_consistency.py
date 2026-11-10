"""Bounded public GET samples to distinguish registry churn from card failure."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import time
import httpx
from r3.state import ROOT, read_json


def probe(url):
    started = time.monotonic()
    try:
        r = httpx.get(url, timeout=6, trust_env=False,
                      headers={'Cache-Control': 'no-cache'})
        result = {'status': r.status_code, 'seconds': time.monotonic()-started,
                  'headers': {k: r.headers[k] for k in
                              ['date', 'server', 'cf-ray', 'cf-cache-status', 'age'] if k in r.headers}}
        if r.status_code == 200:
            body = r.json()
            if url.endswith('/api/shops'):
                rows = body.get('shops', [])
                result.update(count=len(rows), members=sorted(r.get('name', '') for r in rows),
                              own=[{k: s.get(k) for k in ['name', 'url']} for s in rows
                                   if s.get('name') == 'seochanit'])
            elif url.endswith('/api/board'):
                rows = body.get('shops', [])
                result.update(alive_count=sum(s.get('alive') is True for s in rows),
                              own=[{k: s.get(k) for k in ['name', 'alive', 'deals']} for s in rows
                                   if s.get('name') == 'seochanit'])
            else:
                result['name'] = body.get('name')
        return result
    except Exception as exc:
        return {'error': type(exc).__name__, 'seconds': time.monotonic()-started}


if __name__ == '__main__':
    url = read_json(ROOT/'.market/shop-health.json', {})['url'].rstrip('/')
    paths = ['https://market.ourolabs.ai/api/shops',
             'https://market.ourolabs.ai/api/board', url+'/.well-known/agent-card.json']
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = ROOT/'logs'/f'market-discovery-consistency-{stamp}.jsonl'
    print('SOURCE', path, flush=True)
    with path.open('x', encoding='utf-8') as out, ThreadPoolExecutor(max_workers=3) as pool:
        for i in range(12):
            rows = list(pool.map(probe, paths))
            row = dict(at=datetime.now(timezone.utc).isoformat(), registry=rows[0],
                       board=rows[1], card=rows[2])
            out.write(json.dumps(row)+'\n'); out.flush()
            print(json.dumps({'at': row['at'], 'shops': rows[0].get('count'),
                              'listed': bool(rows[0].get('own')), 'board': rows[1].get('own'),
                              'card': rows[2].get('status')}), flush=True)
            if i < 11:
                time.sleep(5)
