"""Read-only progress comparison from frozen public verification and local journal.

No model, registration, order or policy changes. Rank uses the separately dated
local public-board snapshot; event comparisons end at the verification cutoff.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import sqlite3
import sys
from r3.state import ROOT, read_json


def inspect(current_path, baseline_path):
    current=read_json(current_path,{})
    baseline=read_json(baseline_path,{})
    cutoff=datetime.fromisoformat(current['local']['checked_at']).timestamp()
    start=datetime.fromisoformat(baseline['local']['checked_at']).timestamp()
    epoch=current['local']['epoch']
    def own(source,role):return source['public'][2]['data'][role][0]
    comparison={}
    for role,fields in [('shops',['deals','revenue','profit','book','stock_units','all']),
                        ('buyers',['deals','surplus','all'])]:
        a,b=own(baseline,role),own(current,role)
        comparison[role]={k:{'before':a.get(k),'after':b.get(k),
            'change':b[k]-a[k] if isinstance(b.get(k),(int,float)) and isinstance(a.get(k),(int,float)) else None}
            for k in fields}
    board_path=ROOT/'.market/board.json'
    board=read_json(board_path,{})
    ranks={}
    for role,field,key in [('shops','all','name'),('buyers','all','name'),('players','total','player')]:
        rows=board[role];me=next(r for r in rows if r.get(key)=='seochanit')
        leader=max(rows,key=lambda r:r.get(field,0))
        ranks[role]={'rank':1+sum(r.get(field,0)>me.get(field,0) for r in rows),
            'population':len(rows),'own':me[field],'leader':leader[key],
            'leader_score':leader[field],'gap':leader[field]-me[field]}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        def events(kind,since):
            return [dict(json.loads(data),_ts=ts) for ts,data in conn.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts',
                (kind,since,cutoff))]
        def summarize(since):
            sales={e['task']:e for e in events('shop_settled',since)}
            known=[e for e in sales.values() if isinstance(e.get('cost'),(int,float))]
            purchases={e['order']:e for e in events('purchase',since)}
            final={e['order']:e for e in events('market_result',since)}
            retries=events('checkout_attempt',since)
            ids={e['order'] for e in retries}
            return {'shop':{'sales':len(sales),'known_cost':len(known),
                'revenue':sum(e['price'] for e in sales.values()),
                'cost':sum(e['cost'] for e in known),
                'gross_margin_before_later_costs':sum(e['price']-e['cost'] for e in known),
                'below_cost':sum(e['price']<e['cost'] for e in known),
                'warranty_hours':dict(Counter(str(e.get('warranty_hours')) for e in sales.values())),
                'settlements':list(sales.values())},
                'buyer':{'purchases':len(purchases),
                    'initial_value_minus_price':sum(e.get('value',0)-e['price'] for e in purchases.values()),
                    'purchases_by_kind':dict(Counter(e.get('kind') for e in purchases.values())),
                    'final_results':len(final),'verified_results':sum(e.get('verified') is True for e in final.values()),
                    'retry_attempts':len(retries),'orders_with_refusal_retry':len(ids),
                    'retry_orders_later_paid':sum(oid in purchases for oid in ids)},
                'errors':{k:len(events(k,since)) for k in ['market_control_error','strategy_error','merchant_context_error','forum_error']}}
        intervals={'since_previous_report':summarize(start),'since_revision_epoch':summarize(epoch)}
        evaluations=[{k:e.get(k) for k in ['_ts','version','action','reason','evidence']}
            for e in events('strategy_evaluation',epoch)]
    local=current['local']
    inventory=read_json(ROOT/'.market/inventory.json',{})
    catalog=read_json(ROOT/'.market/catalog.json',{})
    result={'sources':{'current':str(current_path),'baseline':str(baseline_path),
        'rank_board_checked_at':datetime.fromtimestamp(board_path.stat().st_mtime,timezone.utc).isoformat(),
        'rank_board_rows':{k:[{f:r.get(f) for f in ['name','player','all','total','house','alive']}
            for r in board[k]] for k in ['shops','buyers','players']}},
        'period':{'start':baseline['local']['checked_at'],'end':local['checked_at'],
            'minutes':(cutoff-start)/60,'epoch':epoch},'comparison':comparison,'ranks':ranks,
        'intervals':intervals,'cash':{'before':baseline['local']['cash'],'after':local['cash'],
            'change':local['cash']-baseline['local']['cash'],'capacity':local['cash_capacity']},
        'control':{k:local['control'].get(k) for k in ['status','at','pause_restock','reasons','leader_gap']},
        'strategies':local['strategies'],'evaluations':evaluations,
        'stock_snapshot':{'checked_at':datetime.now(timezone.utc).isoformat(),
            'balance':inventory.get('balance'),'supply_window':inventory.get('restock'),
            'units':[{'sku':u['sku'],'cost':u.get('cost'),
                'name':catalog.get(u['sku'],{}).get('name')} for u in inventory.get('units',[])]},
        'quality_epoch_mature':local['quality_epoch_mature'],
        'limitations':['Board rank timestamp is separate from comparison cutoff.',
            'Initial buyer value minus price is not mature quality-adjusted reward.',
            'Shop gross margin excludes later storage, faults and refunds.',
            'Final results include no-purchase outcomes; they are not all checkout failures.']}
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current',type=lambda p:ROOT/p,required=True)
    parser.add_argument('--baseline',type=lambda p:ROOT/p,required=True)
    args=parser.parse_args();result=inspect(args.current,args.baseline)
    path=ROOT/'logs'/('market-progress-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    printable=dict(result,sources={k:v for k,v in result['sources'].items() if k!='rank_board_rows'})
    for interval in printable['intervals'].values():interval['shop'].pop('settlements')
    print(json.dumps(printable,ensure_ascii=False,indent=2))
