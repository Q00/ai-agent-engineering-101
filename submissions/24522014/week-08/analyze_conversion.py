"""Snapshot public ledger evidence for our shop's R2 conversion analysis."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl

import httpx


def main():
    root = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = root / 'logs/conversion-analysis' / stamp
    out.mkdir(parents=True)
    with httpx.Client(verify=ssl.create_default_context(), timeout=60) as client:
        def get(path):
            r = client.get('https://market.ourolabs.ai' + path)
            r.raise_for_status()
            return r.json()
        board = get('/api/board?round=2')
        shop = next(s for s in board['shops'] if s['name'] == 'kcwarni')
        last = get('/api/ledger?after=-1')[0]['seq']
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda n: get(f'/api/ledger?after={n}'), range(0, last, 500)))
    entries = sorted({e['seq']: e for page in pages for e in page if e['seq'] <= last}.values(), key=lambda e:e['seq'])
    assert len(entries) == last, (len(entries), last)
    urls = {e['data']['url'] for e in entries if e['kind'] == 'join' and e['data'].get('name') == 'kcwarni'
            and e['data'].get('role') == 'shop'} | {shop['card']['url']}
    results = [e for e in entries if e['round'] == 2 and e['kind'] == 'result' and e['data'].get('shop') in urls]
    order_ids = {e['data'].get('order') for e in results}
    orders = [e for e in entries if e['kind'] == 'order' and e['data'].get('order') in order_ids]
    lookup = {e['data']['order']: e['data'] for e in orders}
    (out/'public-evidence.json').write_text(json.dumps({'snapshot_seq':last,'shop':shop,'urls':sorted(urls),
        'results':results,'orders':orders},ensure_ascii=False,indent=2),encoding='utf-8')
    counts = Counter()
    categories = {}
    items = {}
    examples = {}
    reserves = {'lamp':100,'chair':180,'bookshelf':260}
    openings = {'lamp':120,'chair':210,'bookshelf':300}
    for e in results:
        d=e['data']; order=lookup.get(d.get('order'),{}); item=order.get('item','unknown'); budget=order.get('budget')
        counted=bool(d.get('verified'))
        counts['results']+=1; counts['counted' if counted else 'not_counted']+=1
        stats=items.setdefault(item,Counter());stats['results']+=1;stats['counted' if counted else 'not_counted']+=1
        if type(budget) is int and item in reserves:
            for key,condition in [('below_reserve',budget<reserves[item]),('below_opening',budget<openings[item]),
                                  ('affordable',budget>=reserves[item])]:
                if condition:counts[key]+=1;stats[key]+=1;stats[key+('_counted' if counted else '_failed')]+=1
        if counted:continue
        review=d.get('review') or ''; lower=review.lower()
        if budget is not None and item in reserves and budget<reserves[item]:cat='budget_below_reserve'
        elif '0 offer(s)' in review and 'first ask None' in review:cat='price_unread_no_offers'
        elif '429' in review:cat='buyer_rate_limit'
        elif d['buyer']=='mjforge':cat='reported_terms_or_protocol_mismatch'
        elif d.get('receipt') is not None:cat='receipt_not_verified'
        elif any(s in lower for s in ['timeout','timed out','connection','connecterror','502','503','504','rate limit','harness failed','failed to trade','decode error','did not answer properly','task_state_failed']):cat='technical_or_buyer_runtime'
        elif any(s in lower for s in ['budget','price','no deal','agreement','negotia','walk','reject','reserve','afford','offer']):cat='negotiation_or_price'
        else:cat='unclassified'
        categories[cat]=categories.get(cat,0)+1
        examples.setdefault(cat,[])
        if len(examples[cat])<12:examples[cat].append({'seq':e['seq'],'buyer':d['buyer'],'order':order,'review':review,'checks':d.get('checks')})
    summary={'checked_at':datetime.now(timezone.utc).isoformat(),'directory':str(out),'snapshot_seq':last,
             'board':shop,'counts':counts,'items':items,'failure_categories':categories,'examples':examples,
             'buyers':Counter(e['data']['buyer'] for e in results),
             'failed_buyers':Counter(e['data']['buyer'] for e in results if not e['data'].get('verified'))}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
