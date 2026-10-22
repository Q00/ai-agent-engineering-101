"""Compare observed event phases without trading or changing live policy."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import ssl
import httpx

from measure_sales_experiment import summarize

ROOT = Path(__file__).resolve().parent


def main():
    marketing = json.loads((ROOT / '.market/marketing-deployment.json').read_text(encoding='utf-8'))
    # Original snapshot retains the true event boundary despite later URL updates.
    original = sorted((ROOT / 'logs/review-promise-event').glob('transition-before-*.json'))
    event = json.loads(original[-1].read_text(encoding='utf-8'))
    print('Event snapshot keys:', list(event), flush=True)
    event_cutoff = event['cutoff_seq']
    with httpx.Client(verify=ssl.create_default_context(), timeout=50) as client:
        def get(path):
            response = client.get('https://market.ourolabs.ai' + path)
            response.raise_for_status()
            return response.json()
        last = get('/api/ledger?after=-1')[0]['seq']
        board = get('/api/board?round=2')
        with ThreadPoolExecutor(max_workers=8) as pool:
            pages = list(pool.map(lambda seq: get(f'/api/ledger?after={seq}'), range(0, last, 500)))
    entries = sorted({row['seq']: row for page in pages for row in page if row['seq'] <= last}.values(), key=lambda row: row['seq'])
    assert len(entries) == last
    urls = {row['data']['url'] for row in entries if row['kind'] == 'join'
            and row['data'].get('name') == 'kcwarni' and row['data'].get('role') == 'shop'}
    urls.update((marketing['url'], marketing['previous_url']))
    diagnostics = set()
    for path in (ROOT / 'logs/sales-experiment').glob('public-diagnostic-*.json'):
        diagnostics.update(row['task'] for row in json.loads(path.read_text(encoding='utf-8')).get('results', []))
    pledges = [json.loads(path.read_text(encoding='utf-8')) for path in (ROOT / 'logs/review-promises').glob('*.json')]
    genuine = {row['task']: row for row in pledges if row['task'] not in diagnostics}
    rows = [row for row in entries if row['round'] == 2 and row['kind'] == 'result'
            and row['data'].get('shop') in urls and row['data'].get('receipt', {}).get('task') not in diagnostics]
    orders = {row['data']['order']: row['data'] for row in entries if row['kind'] == 'order'}
    post = [row for row in rows if row['seq'] > marketing['cutoff_seq'] and row['data']['shop'] == marketing['url']]
    pre = [row for row in rows if event_cutoff < row['seq'] <= marketing['cutoff_seq']
           and row['data']['shop'] == marketing['previous_url']]
    legacy = [row for row in rows if row['seq'] <= event_cutoff][-len(pre):]
    phases = {'previous_cashback_recent': legacy, 'promise_before_text_marketing': pre, 'promise_with_text_marketing': post}
    def details(group):
        result = summarize(group, orders)
        sales = [row for row in group if row['data'].get('verified')]
        result.update(first_ts=group[0]['ts'] if group else None, last_ts=group[-1]['ts'] if group else None,
                      floor_price_sales=sum(row['data']['receipt']['price'] == {'lamp':100,'chair':180,'bookshelf':260}[row['data']['receipt']['item']] for row in sales),
                      pledged_sales=sum(row['data']['receipt']['task'] in genuine for row in sales),
                      rating_by_buyer={buyer: {'sales':len(matches), 'mean_stars':sum(row['data']['stars'] for row in matches)/len(matches),
                                               'stars':dict(Counter(str(row['data']['stars']) for row in matches))}
                                       for buyer in sorted({row['data']['buyer'] for row in sales})
                                       if (matches := [row for row in sales if row['data']['buyer'] == buyer])},
                      by_item={item:summarize([row for row in group if orders.get(row['data'].get('order'),{}).get('item') == item], orders)
                               for item in ('lamp','chair','bookshelf')})
        return result
    result = {'checked_at':datetime.now(timezone.utc).isoformat(), 'last_seq':last, 'event_cutoff':event_cutoff,
              'marketing_cutoff':marketing['cutoff_seq'], 'diagnostic_pledges':sum(row['task'] in diagnostics for row in pledges),
              'genuine_pledges':len(genuine), 'current_shop':next(row for row in board['shops'] if row['name'] == 'kcwarni'),
              'phases':{name:details(group) for name,group in phases.items()},
              'limitations':'Observational comparison; buyer mix, budgets and code changed. Legacy uses the same result count as pre-marketing, not equal time. Delayed results can cross boundaries. Reserve surplus excludes costs. No causal attribution.'}
    out = ROOT / 'logs/marketing' / ('effect-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir()
    (out / 'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    ids = {row['data'].get('order') for group in phases.values() for row in group}
    (out / 'evidence.json').write_text(json.dumps({'phases':phases,'orders':[value for key,value in orders.items() if key in ids],
                                                 'pledges':pledges,'diagnostic_tasks':sorted(diagnostics)},ensure_ascii=False,indent=2),encoding='utf-8')
    print('Evidence:', out)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
