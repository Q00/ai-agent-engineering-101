"""Observe counted sales for the new deployment, without trading or writing reviews."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl

import httpx

ROOT = Path(__file__).resolve().parent
RESERVES = {"lamp": 100, "chair": 180, "bookshelf": 260}


def summarize(rows, orders):
    counted = [e for e in rows if e['data'].get('verified')]
    prices = [e['data']['receipt']['price'] for e in counted]
    stars = [e['data']['stars'] for e in counted if type(e['data'].get('stars')) in (int, float)]
    affordable = [e for e in rows if e['data'].get('order') in orders and
                  orders[e['data']['order']]['budget'] >= RESERVES[orders[e['data']['order']]['item']]]
    return {'visits_as_results':len(rows),'counted_sales':len(counted),
            'conversion':len(counted)/len(rows) if rows else None,
            'affordable_visits':len(affordable),
            'affordable_conversion':sum(bool(e['data'].get('verified')) for e in affordable)/len(affordable) if affordable else None,
            'unknown_order_budgets':sum(e['data'].get('order') not in orders for e in rows),
            'mean_price':sum(prices)/len(prices) if prices else None,
            'mean_stars':sum(stars)/len(stars) if stars else None,
            'star_distribution':Counter(map(str, stars)),
            'total_price_minus_reserve':sum(e['data']['receipt']['price']-RESERVES[e['data']['receipt']['item']] for e in counted),
            'buyers':Counter(e['data']['buyer'] for e in rows),
            'enough_for_initial_review':len(rows)>=100}


def main():
    deployment = json.loads((ROOT/'.market/experiment-deployment.json').read_text(encoding='utf-8'))
    cutoff = deployment['cutoff_seq']
    baseline = json.loads((ROOT/'logs/conversion-analysis/20261021T025416Z/public-evidence.json').read_text(encoding='utf-8'))
    with httpx.Client(verify=ssl.create_default_context(),timeout=50) as client:
        def get(path):
            r=client.get('https://market.ourolabs.ai'+path);r.raise_for_status();return r.json()
        last=get('/api/ledger?after=-1')[0]['seq']
        # Include older orders whose results may land across the saved baseline boundary.
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages=list(pool.map(lambda seq:get(f'/api/ledger?after={seq}'),range(0,last,500)))
    entries=sorted({e['seq']:e for page in pages for e in page if e['seq']<=last}.values(),key=lambda e:e['seq'])
    assert len(entries)==last
    urls=set(baseline['urls'])|{deployment['url']}
    urls.update(e['data']['url'] for e in entries if e['kind']=='join' and e['data'].get('name')=='kcwarni' and e['data'].get('role')=='shop')
    rows=[e for e in entries if e['kind']=='result' and e['round']==2 and e['data'].get('shop') in urls]
    before=[e for e in rows if e['seq']<=cutoff]
    after=[e for e in rows if e['seq']>cutoff and e['data']['shop']==deployment['url']]
    orders={e['data']['order']:e['data'] for e in entries if e['kind']=='order'}
    ids={e['data'].get('order') for e in rows}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=ROOT/'logs/sales-experiment'/f'measurement-{stamp}';out.mkdir()
    summary={'checked_at':datetime.now(timezone.utc).isoformat(),'cutoff_seq':cutoff,'last_seq':last,
             'before':summarize(before,orders),'after':summarize(after,orders),
             'after_by_item':{item:summarize([e for e in after if orders.get(e['data'].get('order'),{}).get('item')==item],orders) for item in RESERVES},
             'limitations':'Before and after buyers/budgets differ. Small samples and cumulative averages do not prove improvement. Price-minus-reserve excludes model costs and cashback.'}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'public-evidence.json').write_text(json.dumps({'deployment':deployment,'results':rows,'orders':[o for i,o in orders.items() if i in ids]},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Evidence:',out)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
