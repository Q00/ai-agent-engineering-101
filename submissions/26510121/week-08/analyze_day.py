"""Read-only day diagnosis. Freeze local snapshots; never call models or trade.

Reproduce with --source logs/day-source-<timestamp>.json. Journal reads use a
read-only SQLite transaction and the frozen cutoff. Existing logs are untouched.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
OWNER = 'seochanit'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def stamp(ts):
    return datetime.fromtimestamp(ts, KST).isoformat()


def own(board, table):
    return next(r for r in board[table] if r.get('name') == OWNER)


def freeze():
    files = ['board.json', 'inventory.json', 'shop-health.json', 'runtime.json', 'catalog.json']
    result = {'captured_at': stamp(time.time()), 'local_files': {}}
    for name in files:
        path = ROOT / '.market' / name
        for _ in range(5):
            before = path.stat().st_mtime_ns
            data = read(path)
            after = path.stat().st_mtime_ns
            if before == after:
                break
        else:
            raise RuntimeError('Continuously changing snapshot: ' + name)
        result['local_files'][name] = {'mtime': after / 1e9, 'data': data}
    result['cutoff'] = result['local_files']['board.json']['mtime']
    result['baseline'] = 'logs/market-analysis-source-20261104T033721293560Z.json'
    result['inventory_baseline'] = 'logs/recovery-gap-source-20261104T093629867298Z.json'
    result['policy'] = read(ROOT / 'policy.json')
    return result


def analyze(source):
    start_source = read(ROOT / source['baseline'])
    start = datetime.fromisoformat(start_source['checked_at']).timestamp()
    end = source['cutoff']
    files = source['local_files']
    board, inv, catalog = [files[n]['data'] for n in ['board.json', 'inventory.json', 'catalog.json']]
    cfg = source['policy']
    c = sqlite3.connect((ROOT / '.market/r3.sqlite3').as_uri() + '?mode=ro', uri=True)
    c.execute('BEGIN')
    kinds = ['shop_settled', 'public_broken', 'purchase', 'payment', 'buyer_order',
             'attempt', 'market_result', 'stockout', 'restock_decision', 'decision',
             'prediction', 'accounting_snapshot', 'model_usage', 'quote_comparison',
             'shop_opportunity', 'customer_outcome', 'strategy_change']
    events = {k: [dict(json.loads(d), _id=i, _ts=t) for i, t, d in c.execute(
        'SELECT id,ts,data FROM events WHERE kind=? AND ts<=? ORDER BY ts,rowid', (k, end))]
        for k in kinds}
    kv = {k: json.loads(v) for k, v in c.execute(
        "SELECT key,value FROM kv WHERE key IN ('adaptive:shop','adaptive:buyer','objective_revision')")}
    c.close()
    day = {k: [e for e in es if start < e['_ts'] <= end] for k, es in events.items()}
    result = {'window': {'start': stamp(start), 'end': stamp(end), 'hours': (end-start)/3600},
              'snapshot_times': {k: stamp(v['mtime']) for k, v in files.items()},
              'current_strategy': kv, 'policy': cfg}
    result['board'] = {}
    for table, metrics in [('shops', ['deals','visits','revenue','profit','all','stars','refunds','broken']),
                           ('buyers', ['orders','deals','surplus','all','forum','posts'])]:
        a, b = own(start_source['board'], table), own(board, table)
        result['board'][table] = {'start': a, 'end': b,
            'delta': {k: b[k]-a[k] for k in metrics if type(a.get(k)) in (int,float) and type(b.get(k)) in (int,float)}}
        peers = {r['name']: r for r in start_source['board'][table]}
        comparison = []
        for r in board[table]:
            a = peers.get(r['name'])
            if not a or r.get('house') or not a.get('alive') or not r.get('alive') or a.get('deals',0)<20:
                continue
            comparison.append({'name': r['name'], 'all': r['all'],
                'delta': {k: r.get(k,0)-a.get(k,0) for k in
                          (['deals','visits','revenue','profit'] if table=='shops' else ['orders','deals','surplus'])},
                'stock_units':r.get('stock_units'), 'stock_cost':r.get('stock_cost'),'book':r.get('book')})
        metric = 'profit' if table=='shops' else 'surplus'
        result['board'][table]['matched_peers'] = sorted(comparison,key=lambda x:x['delta'][metric],reverse=True)
        valid = [r for r in board[table] if r.get('deals',0)>0]
        result['board'][table]['rank'] = {k: {'rank':1+sum(r[k]>b[k] for r in valid if type(r.get(k)) in (int,float)),
            'n':sum(type(r.get(k)) in (int,float) for r in valid)} for k in
            (['all','profit','revenue','book','stock_cost'] if table=='shops' else ['all','surplus','deals']) if k in b}
    sales = day['shop_settled']
    all_sold = {e['unit']:e for e in events['shop_settled']}
    refunds = [e for e in day['public_broken'] if e.get('unit') in all_sold and e.get('refunded',0)>0]
    known = [e for e in sales if type(e.get('cost')) in (int,float)]
    losses = [e for e in known if e['price']<e['cost']]
    gross, returned = sum(e['price'] for e in sales), sum(e['refunded'] for e in refunds)
    delta = result['board']['shops']['delta']
    result['shop_accounting'] = {'sales':len(sales), 'gross_sales':gross,'refunds':len(refunds),
        'refund_amount':returned,'net_revenue_recomputed':gross-returned,
        'board_revenue_residual':delta['revenue']-(gross-returned),
        'board_deals_residual':delta['deals']-len(sales), 'cost_known_sales':len(known),
        'sold_cost':sum(e['cost'] for e in known), 'gross_margin_before_refunds':sum(e['price']-e['cost'] for e in known),
        'below_cost_sales':len(losses),'below_cost_loss':sum(e['cost']-e['price'] for e in losses),
        'below_cost_fraction':len(losses)/len(known) if known else None,
        'largest_below_cost_transactions':[dict(e, name=catalog.get(e['sku'],{}).get('name'),
            loss=e['cost']-e['price']) for e in sorted(losses,key=lambda e:e['cost']-e['price'],reverse=True)[:10]],
        'revenue_minus_profit_delta':delta['revenue']-delta['profit'],
        'cash_start':own(start_source['board'],'shops')['profit']+5000000,
        'cash_end_at_board':own(board,'shops')['profit']+5000000}
    def sale_group(es):
        return {'n':len(es),'sales':sum(e['price'] for e in es),'cost':sum(e.get('cost') or 0 for e in es),
                'below_cost_n':sum(e['price']<e['cost'] for e in es if e.get('cost') is not None),
                'margin':sum(e['price']-e['cost'] for e in es if e.get('cost') is not None)}
    by_sku = defaultdict(list)
    for e in known: by_sku[e['sku']].append(e)
    result['shop_skus'] = sorted([dict(sku=s,name=catalog.get(s,{}).get('name'),
        kind=catalog.get(s,{}).get('kind'),**sale_group(es)) for s,es in by_sku.items()],key=lambda r:r['margin'])
    result['shop_kinds'] = {k:sale_group([e for e in known if catalog.get(e['sku'],{}).get('kind')==k]) for k in ['food','goods']}
    now_inv = files['inventory.json']['mtime']
    # Apply the production cash rule with read-only inputs and no journal writes.
    sys.path.insert(0,str(ROOT))
    from r3.economy import cash_capacity
    class JournalView:
        def events(self,kind,limit=500):return list(reversed(events.get(kind,[])))[:limit]
    capacity = cash_capacity(inv,JournalView(),cfg,now_inv)
    units = inv['units']
    units_by_sku = defaultdict(list)
    for u in units:units_by_sku[u['sku']].append(u)
    result['inventory'] = {'balance':inv['balance'],'units':len(units),'sku_count':len(units_by_sku),
        'acquisition_cost':sum(u['cost'] for u in units),'capacity':capacity,
        'by_kind':{k:{'n':sum(u['kind']==k for u in units),'cost':sum(u['cost'] for u in units if u['kind']==k)} for k in ['food','goods']},
        'skus':[{'sku':s,'name':catalog.get(s,{}).get('name'),'n':len(us),'cost':sum(u['cost'] for u in us),
                 'age_max_hours':(now_inv-min(u['supplied_at'] for u in us))/3600,
                 'expiry_min_hours':min(((u['expires_at']-now_inv)/3600 for u in us if u.get('expires_at')),default=None)}
                for s,us in units_by_sku.items()]}
    shop = own(board,'shops')
    result['book_identities'] = {'board_profit':shop['profit'],'board_stock_cost':shop.get('stock_cost'),
        'board_book':shop.get('book'),'residual':shop.get('book',0)-shop['profit']-shop.get('stock_cost',0),
        'local_profit':inv['balance']-inv['start']-inv['injected'],
        'local_book':inv['balance']-inv['start']-inv['injected']+sum(u['cost'] for u in units)}
    old=read(ROOT/source['inventory_baseline']); old_inv=old['inventory'];old_shop=own(old['board'],'shops')
    result['inventory_comparison']={'start':old['checked_at'],'hours':(now_inv-datetime.fromisoformat(old['checked_at']).timestamp())/3600,
        'start_cash':old_inv['balance'],'start_units':len(old_inv['units']),
        'start_stock_cost':sum(u['cost'] for u in old_inv['units']),
        'start_book':old_shop['profit']+sum(u['cost'] for u in old_inv['units'])}
    # Full-kit log has HH:MM:SS only. Anchor calendar dates to journal order IDs.
    order_ts={e['order']:e['_ts'] for e in events['buyer_order']}
    log_path=ROOT/'logs/market-kit.log'
    lines=log_path.read_text(encoding='utf-8',errors='replace').splitlines()
    dated=[]; day_date=None;prev_sec=None
    for line in lines:
        m=re.match(r'(\d\d):(\d\d):(\d\d) ',line)
        if not m:continue
        sec=int(m[1])*3600+int(m[2])*60+int(m[3])
        anchor=re.search(r'\[order ([^\]]+)\]',line)
        if anchor and anchor[1] in order_ts:
            day_date=datetime.fromtimestamp(order_ts[anchor[1]],KST).replace(hour=0,minute=0,second=0,microsecond=0)
        elif day_date is not None and prev_sec is not None and prev_sec-sec>43200:
            day_date+=timedelta(days=1)
        prev_sec=sec
        if day_date is not None:
            ts=day_date.timestamp()+sec
            if start<ts<=end:dated.append((ts,line))
    supplies=[]
    for t,line in dated:
        m=re.search(r'\[supply\] (\d+) x (.*?) for (\d+), balance (\d+)',line)
        if m:supplies.append({'at':stamp(t),'qty':int(m[1]),'name':m[2],'cost':int(m[3])})
    result['supply_log']={'n':len(supplies),'units':sum(x['qty'] for x in supplies),'cost':sum(x['cost'] for x in supplies),
        'cash_expense_residual':delta['revenue']-delta['profit']-sum(x['cost'] for x in supplies),
        'log_bytes_at_read':log_path.stat().st_size, 'calendar_method':'HH:MM:SS anchored to journal buyer_order IDs; midnight rollover',
        'records':supplies}
    orders={e['order']:e for e in day['buyer_order'] if e['_ts']<=end-120}
    paid={e['order']:e for e in events['purchase']}
    terminal={e['order'] for e in events['market_result']}
    payments=day['payment']
    reasons=Counter()
    for e in payments:
        reason=e.get('reason','')
        key=('paid' if e.get('paid') else 'shelf_life' if 'shelf life' in reason else
             'unit_unavailable' if 'not in stock' in reason else 'warranty' if 'warranty' in reason else
             'certificate' if 'certificate' in reason else 'other')
        reasons[key]+=1
    result['buyer']={'received_orders':len(day['buyer_order']),'paid':len(day['purchase']),
        'mature_order_cohort':{'received':len(orders),'terminal':len(orders.keys()&terminal),'paid':len(orders.keys()&paid),
            'conversion':len(orders.keys()&paid)/len(orders) if orders else None},
        'payment_attempts':dict(reasons),
        'attempt_reasons':dict(Counter(e.get('reason') or e.get('kind') for e in day['attempt'])),
        'purchase_kind':dict(Counter(e.get('kind') for e in day['purchase'])),
        'last_paid':stamp(day['purchase'][-1]['_ts']) if day['purchase'] else None,
        'largest_inter_purchase_gap_minutes':max([(b['_ts']-a['_ts'])/60 for a,b in zip(day['purchase'],day['purchase'][1:])],default=None)}
    result['restock']={'plans':len(day['restock_decision']),
        'planned_lines':sum(len(e['buy']) for e in day['restock_decision']),
        'stockouts':len(day['stockout']),
        'stockouts_sku':Counter(e.get('sku') for e in day['stockout']).most_common(15),
        'no_sku_stockout_requests':Counter(e.get('request') for e in day['stockout'] if not e.get('sku')).most_common(10),
        'current_target':cfg['inventory_target_units'], 'growth_v5_enabled':cfg.get('shop_growth_v5',False)}
    predictions={e['decision']:e for e in day['prediction']}
    restock_decisions=[e for e in day['decision'] if any('buy' in x for x in predictions.get(e['decision'],{}).get('candidates',[]))]
    result['restock']['chosen']=dict(Counter(e.get('chosen') for e in restock_decisions))
    result['model_usage']={'calls':len(day['model_usage']), 'estimated_usd':sum(e.get('estimated_usd',0) for e in day['model_usage'])}
    result['hourly']=[]
    hour=start
    while hour<end:
        finish=min(hour+3600,end)
        ss=[e for e in sales if hour<e['_ts']<=finish];pp=[e for e in day['purchase'] if hour<e['_ts']<=finish]
        rr=[e for e in refunds if hour<e['_ts']<=finish]
        acct=[e for e in events['accounting_snapshot'] if e['_ts']<=finish]
        result['hourly'].append({'start':stamp(hour),'end':stamp(finish),'sales':len(ss),'purchases':len(pp),
            'net_sales':sum(e['price'] for e in ss)-sum(e['refunded'] for e in rr),
            'below_cost_sales':sum(e['price']<e['cost'] for e in ss if e.get('cost') is not None),
            'cash_observed':acct[-1]['balance'] if acct else None})
        hour=finish
    result['checks']={'board_book_identity':result['book_identities']['residual']==0,
        'sales_count_matches_board':result['shop_accounting']['board_deals_residual']==0,
        'sales_net_revenue_matches_board':result['shop_accounting']['board_revenue_residual']==0,
        'cost_known_for_all_sales':len(known)==len(sales)}
    result['limitations']=['Snapshots update independently; inventory and board may differ by seconds.',
        '24h comparison uses nearest preserved baseline; exact duration is stated.',
        'Baseline board did not expose stock_cost/book; inventory/book comparison starts at the first preserved full inventory.',
        'Revenue minus cash-profit change includes all supply spending; it is not profit margin.',
        'Peer alive at both endpoints does not establish continuous uptime.',
        'Buyer surplus can still change as recent goods break within six hours.',
        'Public ledger ingestion delay and subsecond snapshot boundaries can leave small reconciliation residuals.']
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    args=parser.parse_args()
    label=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    source=read(args.source) if args.source else freeze()
    source_path=args.source or ROOT/'logs'/('day-source-'+label+'.json')
    if not args.source:source_path.write_text(json.dumps(source,ensure_ascii=False,indent=2),encoding='utf-8')
    result=analyze(source)
    result['source']=str(source_path.resolve().relative_to(ROOT))
    output=ROOT/'logs'/('day-analysis-'+label+'.json')
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',source_path)
    print('RESULT',output)
    for key in ['window','shop_accounting','book_identities','inventory_comparison','buyer','restock','model_usage','checks']:
        print(key,json.dumps(result[key],ensure_ascii=False))
    print('inventory',json.dumps({k:v for k,v in result['inventory'].items() if k!='skus'},ensure_ascii=False))
    print('board_summary',json.dumps({k:{x:v[x] for x in ['delta','rank']} for k,v in result['board'].items()},ensure_ascii=False))
    print('supply',json.dumps({k:v for k,v in result['supply_log'].items() if k!='records'},ensure_ascii=False))
    print('loss_skus',json.dumps(result['shop_skus'][:8],ensure_ascii=False))
