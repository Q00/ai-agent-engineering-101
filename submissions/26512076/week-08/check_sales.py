"""Print observed sales stages; absence of trace before installation is unknown, not failure."""
import collections
import json
from pathlib import Path

def rows(path):
    if not path.exists():return []
    result=[]
    for line in path.read_text(encoding='utf-8-sig',errors='replace').splitlines():
        try:result.append(json.loads(line))
        except ValueError:pass
    return result

def main():
    root=Path(__file__).resolve().parent
    sellers=rows(root/'logs'/'r3-seller.jsonl')
    # r3_common audit logs are in logs/; locate its configured path if supplied elsewhere.
    if not sellers:
        sellers=rows(root/'r3-seller.jsonl')
    traces=rows(root/'.market'/'seller-checkout.jsonl')
    grouped=collections.defaultdict(list)
    for row in traces:grouped[row.get('task')].append(row)
    agreements={r['task']:r for r in sellers if r.get('event')=='agreement_not_settlement' and r.get('task')}
    counts=collections.Counter()
    for task,row in agreements.items():
        events=grouped.get(task,[]);names={e['event'] for e in events}
        if 'checkout_completed' in names:status='SETTLED'
        elif 'checkout_canceled' in names:status='CANCELED: buyer canceled; exact buyer-side reason unknown'
        elif 'checkout_rejected' in names:
            e=next(e for e in reversed(events) if e['event']=='checkout_rejected')
            status='REJECTED: '+str(e.get('reason'))+' '+str(e.get('failed_checks',[]))
        elif 'checkout_created' in names:status='CHECKOUT OPEN: no completion recorded; may still be pending'
        elif 'checkout_request' in names:status='REQUEST SEEN: response/completion unknown'
        elif 'receipt_observed' in names:status='NO CHECKOUT REQUEST RECORDED after receipt'
        else:status='UNKNOWN: agreement has no trace coverage (possibly before installation)'
        counts[status.split(':')[0]]+=1
        rec=row.get('receipt',{});print(task,rec.get('item'),rec.get('price'),status)
    print('Sales stages:',dict(counts))
    print('Seller decisions:',dict(collections.Counter(r.get('event') for r in sellers if r.get('event') in
          ['no_stock','conditions_unavailable','loss_prevented','stock_conflict','failed','seller_declined','negotiation_closed'])))
    if not traces:print('No seller checkout trace yet. Install patch and restart kit first.')
if __name__=='__main__':main()
